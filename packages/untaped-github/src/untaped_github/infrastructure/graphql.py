"""The batched repository ref probe over aliased GraphQL queries.

This module runs the probe: chunking, alias bookkeeping, per-repo ref
pagination, and transient 5xx/transport retry splitting. The query text
and how answers read live in
:mod:`untaped_github.infrastructure.graphql_queries`, and how failures read
in :mod:`untaped_github.infrastructure.graphql_errors`. ``GithubClient``
only wires an ``HttpClient`` and the derived GraphQL endpoint into
:func:`fetch_repo_refs`.

Each chunk of repositories becomes one POST with aliases ``r0..rN`` that
map back to input order. Repositories GitHub reports as ``NOT_FOUND`` or
``FORBIDDEN`` arrive as a ``null`` data node plus an ``errors`` entry
with ``path: ["rX"]``; those are collected, not raised. Transient
HTTP 5xx and transport failures are narrowed into per-repo
``BatchRepoRefsFailure`` results when possible. Global GraphQL access
failures raise :class:`untaped_github.GithubGraphqlError`.

GraphQL cost: roughly one point per repo per ref connection, against a
separate 5000 points/hour budget. A full heads+tags probe of 1500 repos
costs ~3000 points; ``kinds=("heads",)`` halves that by omitting the
tags connection from the query entirely.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from itertools import batched
from typing import Any, NamedTuple

from untaped.sdk import (
    HttpClient,
    HttpError,
    HttpTransportError,
    RetryPolicy,
    Service,
    UntapedError,
    bounded_map,
)
from untaped_github.domain.models import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    RefKind,
    RepoRef,
    RepoRefs,
)
from untaped_github.errors import GithubGraphqlError
from untaped_github.infrastructure.graphql_errors import classify_errors, graphql_http_error
from untaped_github.infrastructure.graphql_queries import (
    RateLimit,
    RepoTarget,
    batch_query,
    default_branch_query,
    merge_rate_limit,
    page_query,
    parse_ref_nodes,
    parse_repo_target,
    peel_oid,
    reported_rate_limit,
    validate_kinds,
)
from untaped_github.settings import GithubSettings

_GRAPHQL_ACCESS_STATUSES = frozenset({401, 403, 429})


# These short delays only catch immediate blips. Sustained transient
# failures are handled by adaptive splitting and by callers rerunning.
# A probe query only reads, so a resent POST is safe: retry 5xx and transport
# failures twice before the chunk is split.
_TRANSIENT_RETRY = RetryPolicy(
    backoff_base=0.01,
    retry_statuses=tuple(range(500, 600)),
    honor_retry_after=False,
    idempotent_methods=frozenset({"POST"}),
)
_NO_PROGRESS_SPLIT_LOOKAHEAD = 1


type ProgressFn = Callable[[int, int], None]
type _Resolve = Callable[
    [RepoTarget, dict[str, Any]], tuple[RepoRefs | BatchRepoRefsFailure, RateLimit]
]


class _ChunkExecution(NamedTuple):
    payloads: list[tuple[tuple[RepoTarget, ...], dict[str, Any]]]
    failures: list[BatchRepoRefsFailure]


def graphql_endpoint(base_url: str) -> str:
    """Derive the absolute GraphQL endpoint from a REST ``base_url``.

    ``https://api.github.com`` → ``https://api.github.com/graphql``;
    GHE ``https://<host>/api/v3`` → ``https://<host>/api/graphql``. The
    URL must be absolute: the underlying httpx client carries the REST
    ``base_url``, and relative joining would yield ``/api/v3/graphql``
    on GHE.
    """
    base = base_url.rstrip("/")
    if base.endswith("/api/v3"):
        return base.removesuffix("/v3") + "/graphql"
    return base + "/graphql"


def fetch_repo_refs(
    http: HttpClient,
    endpoint: str,
    repos: Sequence[str],
    *,
    kinds: Sequence[str] = ("heads", "tags"),
    chunk_size: int = 50,
    concurrency: int = 1,
    on_progress: ProgressFn | None = None,
) -> BatchRepoRefsResult:
    """Probe refs for ``repos`` in batches of ``chunk_size`` per POST."""
    ref_kinds = validate_kinds(kinds)
    return _probe(
        http,
        endpoint,
        repos,
        chunk_size=chunk_size,
        concurrency=concurrency,
        on_progress=on_progress,
        query=lambda chunk: batch_query(chunk, ref_kinds),
        resolve=lambda target, node: _resolve_repo(http, endpoint, target, node, ref_kinds),
    )


def fetch_default_branch_refs(
    http: HttpClient,
    endpoint: str,
    repos: Sequence[str],
    *,
    chunk_size: int = 100,
    concurrency: int = 1,
    on_progress: ProgressFn | None = None,
) -> BatchRepoRefsResult:
    """Probe only default-branch heads for ``repos`` without ref connections."""
    return _probe(
        http,
        endpoint,
        repos,
        chunk_size=chunk_size,
        concurrency=concurrency,
        on_progress=on_progress,
        query=default_branch_query,
        resolve=lambda target, node: (_resolve_default_branch_repo(target, node), RateLimit()),
    )


def _probe(
    http: HttpClient,
    endpoint: str,
    repos: Sequence[str],
    *,
    chunk_size: int,
    concurrency: int,
    on_progress: ProgressFn | None,
    query: Callable[[tuple[RepoTarget, ...]], str],
    resolve: _Resolve,
) -> BatchRepoRefsResult:
    """POST ``repos`` in chunks, ``concurrency`` at a time, and merge what they read.

    A chunk whose request fails for any reason but a GraphQL error
    gives each of its repos a ``request`` failure; the other chunks go on.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    targets = [parse_repo_target(repo) for repo in repos]
    chunks = list(batched(targets, chunk_size, strict=False))
    results: dict[int, BatchRepoRefsResult] = {}

    def read(index: int) -> BatchRepoRefsResult:
        try:
            return _probe_chunk(http, endpoint, chunks[index], query, resolve)
        except GithubGraphqlError:
            raise
        except UntapedError as exc:
            return BatchRepoRefsResult(failures=_request_failures(chunks[index], exc))

    def record(index: int, result: BatchRepoRefsResult) -> None:
        results[index] = result
        if on_progress is not None:
            on_progress(sum(len(chunks[done]) for done in results), len(targets))

    bounded_map(read, range(len(chunks)), concurrency=concurrency, on_each=record)
    return _merged(results[index] for index in range(len(chunks)))


def _probe_chunk(
    http: HttpClient,
    endpoint: str,
    chunk: tuple[RepoTarget, ...],
    query: Callable[[tuple[RepoTarget, ...]], str],
    resolve: _Resolve,
) -> BatchRepoRefsResult:
    collected: list[RepoRefs] = []
    missing: list[str] = []
    rate_limit = RateLimit()
    execution = _execute_chunk(http, endpoint, chunk, query)
    failures = list(execution.failures)
    for subchunk, payload in execution.payloads:
        missing_aliases = classify_errors(payload)
        data = payload.get("data") or {}
        rate_limit = merge_rate_limit(rate_limit, reported_rate_limit(payload))
        for index, target in enumerate(subchunk):
            alias = f"r{index}"
            node = data.get(alias)
            if node is None:
                if alias not in missing_aliases:
                    raise UntapedError(
                        f"github graphql returned null for {target.full_name} "
                        "without an explanatory error",
                        system=Service(GithubSettings),
                    )
                missing.append(target.full_name)
                continue
            result, page_rate_limit = resolve(target, node)
            rate_limit = merge_rate_limit(rate_limit, page_rate_limit)
            if isinstance(result, BatchRepoRefsFailure):
                failures.append(result)
            else:
                collected.append(result)
    return BatchRepoRefsResult(
        repos=tuple(collected),
        missing=tuple(missing),
        failures=tuple(failures),
        rate_limit_cost=rate_limit.cost,
        rate_limit_remaining=rate_limit.remaining,
        rate_limit_reset_at=rate_limit.reset_at,
    )


def _merged(results: Iterable[BatchRepoRefsResult]) -> BatchRepoRefsResult:
    """One result from the chunks' results, in chunk order."""
    repos: list[RepoRefs] = []
    missing: list[str] = []
    failures: list[BatchRepoRefsFailure] = []
    rate_limit = RateLimit()
    for result in results:
        repos.extend(result.repos)
        missing.extend(result.missing)
        failures.extend(result.failures)
        rate_limit = merge_rate_limit(
            rate_limit,
            RateLimit(
                result.rate_limit_cost, result.rate_limit_remaining, result.rate_limit_reset_at
            ),
        )
    return BatchRepoRefsResult(
        repos=tuple(repos),
        missing=tuple(missing),
        failures=tuple(failures),
        rate_limit_cost=rate_limit.cost,
        rate_limit_remaining=rate_limit.remaining,
        rate_limit_reset_at=rate_limit.reset_at,
    )


def _request_failures(
    chunk: tuple[RepoTarget, ...], exc: UntapedError
) -> tuple[BatchRepoRefsFailure, ...]:
    return tuple(
        BatchRepoRefsFailure(
            full_name=target.full_name,
            reason=str(exc) or type(exc).__name__,
            kind="request",
            category=exc.category,
            status_code=exc.status_code if isinstance(exc, HttpError) else None,
            url=exc.url if isinstance(exc, HttpError) else None,
        )
        for target in chunk
    )


def _execute_chunk(
    http: HttpClient,
    endpoint: str,
    chunk: tuple[RepoTarget, ...],
    query: Callable[[tuple[RepoTarget, ...]], str],
) -> _ChunkExecution:
    """POST one chunk; adaptively isolate retryable transient failures."""
    try:
        payload = _post(http, endpoint, query(chunk))
        return _ChunkExecution([(chunk, payload)], [])
    except HttpError as exc:
        if not _is_transient_probe_error(exc):
            raise
        if len(chunk) == 1:
            return _ChunkExecution([], _transient_failures(chunk, exc))
        return _split_failed_chunk(
            http,
            endpoint,
            chunk,
            query,
            no_progress_split_lookahead=_NO_PROGRESS_SPLIT_LOOKAHEAD,
        )


def _is_server_error(exc: HttpError) -> bool:
    return exc.status_code is not None and exc.status_code >= 500


def _is_transient_probe_error(exc: HttpError) -> bool:
    return _is_server_error(exc) or isinstance(exc, HttpTransportError)


def _split_failed_chunk(
    http: HttpClient,
    endpoint: str,
    chunk: tuple[RepoTarget, ...],
    query: Callable[[tuple[RepoTarget, ...]], str],
    *,
    no_progress_split_lookahead: int,
) -> _ChunkExecution:
    mid = len(chunk) // 2
    attempts: list[tuple[tuple[RepoTarget, ...], dict[str, Any] | HttpError]] = []
    any_success = False
    for half in (chunk[:mid], chunk[mid:]):
        try:
            attempts.append((half, _post(http, endpoint, query(half))))
            any_success = True
        except HttpError as exc:
            if not _is_transient_probe_error(exc):
                raise
            attempts.append((half, exc))
    if not any_success and no_progress_split_lookahead <= 0:
        failed = [
            _transient_failure(target, outcome)
            for half, outcome in attempts
            if isinstance(outcome, HttpError)
            for target in half
        ]
        return _ChunkExecution([], failed)

    payloads: list[tuple[tuple[RepoTarget, ...], dict[str, Any]]] = []
    failures: list[BatchRepoRefsFailure] = []
    for half, outcome in attempts:
        if isinstance(outcome, dict):
            payloads.append((half, outcome))
            continue
        if len(half) == 1:
            failures.extend(_transient_failures(half, outcome))
            continue
        narrowed = _split_failed_chunk(
            http,
            endpoint,
            half,
            query,
            no_progress_split_lookahead=(
                _NO_PROGRESS_SPLIT_LOOKAHEAD if any_success else no_progress_split_lookahead - 1
            ),
        )
        payloads.extend(narrowed.payloads)
        failures.extend(narrowed.failures)
    return _ChunkExecution(payloads, failures)


def _transient_failures(
    chunk: tuple[RepoTarget, ...],
    exc: HttpError,
) -> list[BatchRepoRefsFailure]:
    return [_transient_failure(target, exc) for target in chunk]


def _transient_failure(target: RepoTarget, exc: HttpError) -> BatchRepoRefsFailure:
    return BatchRepoRefsFailure(
        full_name=target.full_name,
        reason=str(exc),
        kind="server_error" if _is_server_error(exc) else "transport",
        status_code=exc.status_code,
        url=exc.url,
    )


def _post(http: HttpClient, endpoint: str, query: str) -> dict[str, Any]:
    try:
        payload = http.post_json(endpoint, json={"query": query}, retry=_TRANSIENT_RETRY)
    except HttpError as exc:
        if exc.status_code in _GRAPHQL_ACCESS_STATUSES:
            raise graphql_http_error(exc) from exc
        raise
    if not isinstance(payload, dict):
        raise HttpError(
            f"expected JSON object from {endpoint}, got {type(payload).__name__}",
            url=endpoint,
        )
    return payload


def _resolve_repo(
    http: HttpClient,
    endpoint: str,
    target: RepoTarget,
    node: dict[str, Any],
    kinds: tuple[RefKind, ...],
) -> tuple[RepoRefs | BatchRepoRefsFailure, RateLimit]:
    """Build one :class:`RepoRefs`, following ref-pagination cursors.

    Repos with >100 refs in a namespace are rare; follow-up single-repo
    queries run serially with ``after: <cursor>`` until exhausted.
    """
    rate_limit = RateLimit()
    refs: list[RepoRef] = []
    for kind in kinds:
        connection = node[kind]
        while True:
            refs.extend(parse_ref_nodes(kind, connection["nodes"]))
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            try:
                payload = _post(http, endpoint, page_query(target, kind, page["endCursor"]))
            except HttpError as exc:
                if not _is_transient_probe_error(exc):
                    raise
                return _transient_failure(target, exc), rate_limit
            # The repo resolved in the batch query, so even NOT_FOUND
            # here (deleted mid-probe) is unexpected enough to raise.
            if classify_errors(payload):
                raise UntapedError(
                    f"github graphql lost access to {target.full_name} during ref pagination",
                    system=Service(GithubSettings),
                )
            rate_limit = merge_rate_limit(rate_limit, reported_rate_limit(payload))
            connection = payload["data"]["r0"][kind]
    default_branch_ref = node.get("defaultBranchRef")
    default_branch = default_branch_ref["name"] if isinstance(default_branch_ref, dict) else None
    return (
        RepoRefs(full_name=target.full_name, default_branch=default_branch, refs=tuple(refs)),
        rate_limit,
    )


def _resolve_default_branch_repo(target: RepoTarget, node: dict[str, Any]) -> RepoRefs:
    default_branch_ref = node.get("defaultBranchRef")
    if not isinstance(default_branch_ref, dict):
        return RepoRefs(full_name=target.full_name, default_branch=None, refs=())
    name = default_branch_ref.get("name")
    branch_name = name if isinstance(name, str) and name else None
    target_node = default_branch_ref.get("target")
    refs = (
        (RepoRef(kind="heads", name=branch_name, sha=peel_oid(target_node)),)
        if branch_name is not None and isinstance(target_node, dict)
        else ()
    )
    return RepoRefs(full_name=target.full_name, default_branch=branch_name, refs=refs)
