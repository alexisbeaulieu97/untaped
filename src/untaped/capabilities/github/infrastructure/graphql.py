"""Aliased GraphQL queries for the batched repository ref probe.

This module isolates GraphQL mechanics — query building, alias
bookkeeping, response parsing, missing-repo classification, per-repo ref
pagination, and transient 5xx/transport retry splitting — the same way
``pagination.py`` isolates REST ``Link``-header mechanics. ``GithubClient``
only wires an ``HttpClient`` and the derived GraphQL endpoint into
:func:`fetch_repo_refs`.

Each chunk of repositories becomes one POST with aliases ``r0..rN`` that
map back to input order. Repositories GitHub reports as ``NOT_FOUND`` or
``FORBIDDEN`` arrive as a ``null`` data node plus an ``errors`` entry
with ``path: ["rX"]``; those are collected, not raised. Transient
HTTP 5xx and transport failures are narrowed into per-repo
``BatchRepoRefsFailure`` results when possible. Global GraphQL access
failures raise :class:`untaped.capabilities.github.GithubGraphqlError`.

GraphQL cost: roughly one point per repo per ref connection, against a
separate 5000 points/hour budget. A full heads+tags probe of 1500 repos
costs ~3000 points; ``kinds=("heads",)`` halves that by omitting the
tags connection from the query entirely.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from itertools import batched
from typing import Any, NamedTuple, cast

from untaped.api import HttpClient, HttpError, HttpTransportError, UntapedError
from untaped.capabilities.github.domain.errors import GithubGraphqlError, GithubGraphqlErrorKind
from untaped.capabilities.github.domain.models import (
    BatchRepoRefsFailure,
    BatchRepoRefsFailureKind,
    BatchRepoRefsResult,
    RefKind,
    RepoRef,
    RepoRefs,
)

_REF_PREFIXES: dict[RefKind, str] = {"heads": "refs/heads/", "tags": "refs/tags/"}
_PAGE_SIZE = 100
_MISSING_ERROR_TYPES = frozenset({"NOT_FOUND", "FORBIDDEN"})
_GRAPHQL_ACCESS_STATUSES = frozenset({401, 403, 429})
_RATE_LIMIT_FIELD = "rateLimit { cost remaining resetAt }"
# These short delays only catch immediate blips. Sustained transient
# failures are handled by adaptive splitting and by callers rerunning.
_TRANSIENT_RETRY_DELAYS = (0.01, 0.02)
_NO_PROGRESS_SPLIT_LOOKAHEAD = 1
# Annotated tags point at a Tag object instead of a commit; peel via
# nested ``target { oid }`` two levels deep (covers tags-of-tags).
_TARGET_FIELD = "target { oid ... on Tag { target { oid ... on Tag { target { oid } } } } }"


class _RepoTarget(NamedTuple):
    full_name: str
    owner: str
    name: str


class _RateLimit(NamedTuple):
    cost: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None


class _ChunkExecution(NamedTuple):
    payloads: list[tuple[tuple[_RepoTarget, ...], dict[str, Any]]]
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
) -> BatchRepoRefsResult:
    """Probe refs for ``repos`` in batches of ``chunk_size`` per POST."""
    ref_kinds = _validate_kinds(kinds)
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
    targets = [_parse_repo_target(repo) for repo in repos]
    collected: list[RepoRefs] = []
    missing: list[str] = []
    failures: list[BatchRepoRefsFailure] = []
    rate_limit = _RateLimit()

    def query(subchunk: tuple[_RepoTarget, ...]) -> str:
        return _build_batch_query(subchunk, ref_kinds)

    for chunk in batched(targets, chunk_size, strict=False):
        execution = _execute_chunk(http, endpoint, chunk, query)
        failures.extend(execution.failures)
        for subchunk, payload in execution.payloads:
            found, gone, failed, chunk_rate_limit = _resolve_chunk(
                http, endpoint, subchunk, ref_kinds, payload
            )
            collected.extend(found)
            missing.extend(gone)
            failures.extend(failed)
            rate_limit = _merge_rate_limit(rate_limit, chunk_rate_limit)
    return BatchRepoRefsResult(
        repos=tuple(collected),
        missing=tuple(missing),
        failures=tuple(failures),
        rate_limit_cost=rate_limit.cost,
        rate_limit_remaining=rate_limit.remaining,
        rate_limit_reset_at=rate_limit.reset_at,
    )


def fetch_default_branch_refs(
    http: HttpClient,
    endpoint: str,
    repos: Sequence[str],
    *,
    chunk_size: int = 200,
) -> BatchRepoRefsResult:
    """Probe only default-branch heads for ``repos`` without ref connections."""
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
    targets = [_parse_repo_target(repo) for repo in repos]
    collected: list[RepoRefs] = []
    missing: list[str] = []
    failures: list[BatchRepoRefsFailure] = []
    rate_limit = _RateLimit()
    for chunk in batched(targets, chunk_size, strict=False):
        execution = _execute_chunk(http, endpoint, chunk, _build_default_branch_query)
        failures.extend(execution.failures)
        for subchunk, payload in execution.payloads:
            found, gone, chunk_rate_limit = _resolve_default_branch_chunk(subchunk, payload)
            collected.extend(found)
            missing.extend(gone)
            rate_limit = _merge_rate_limit(rate_limit, chunk_rate_limit)
    return BatchRepoRefsResult(
        repos=tuple(collected),
        missing=tuple(missing),
        failures=tuple(failures),
        rate_limit_cost=rate_limit.cost,
        rate_limit_remaining=rate_limit.remaining,
        rate_limit_reset_at=rate_limit.reset_at,
    )


def _validate_kinds(kinds: Sequence[str]) -> tuple[RefKind, ...]:
    if not kinds:
        raise ValueError("kinds must not be empty")
    for kind in kinds:
        if kind not in _REF_PREFIXES:
            raise ValueError(f"invalid ref kind {kind!r}: expected 'heads' or 'tags'")
    # Dedupe preserving order: GraphQL merges duplicate aliased
    # connections, so a repeated kind would double every ref.
    # Membership in _REF_PREFIXES is exactly the RefKind literal set.
    return tuple(cast("RefKind", kind) for kind in dict.fromkeys(kinds))


def _parse_repo_target(value: str) -> _RepoTarget:
    # UntapedError, not ValueError: repo strings can originate from user
    # source config (typos), and core ``report_errors`` renders
    # UntapedError as a message instead of a traceback.
    owner, sep, name = value.partition("/")
    if not sep or not owner or not name or "/" in name:
        raise UntapedError(f"invalid repository {value!r}: expected 'owner/name'")
    return _RepoTarget(value, owner, name)


def _execute_chunk(
    http: HttpClient,
    endpoint: str,
    chunk: tuple[_RepoTarget, ...],
    query: Callable[[tuple[_RepoTarget, ...]], str],
) -> _ChunkExecution:
    """POST one chunk; adaptively isolate retryable transient failures."""
    try:
        payload = _post_with_transient_retries(http, endpoint, query(chunk))
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


def _post_with_transient_retries(http: HttpClient, endpoint: str, query: str) -> dict[str, Any]:
    last_exc: HttpError | None = None
    for delay in (0.0, *_TRANSIENT_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return _post(http, endpoint, query)
        except HttpError as exc:
            if not _is_transient_probe_error(exc):
                raise
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def _split_failed_chunk(
    http: HttpClient,
    endpoint: str,
    chunk: tuple[_RepoTarget, ...],
    query: Callable[[tuple[_RepoTarget, ...]], str],
    *,
    no_progress_split_lookahead: int,
) -> _ChunkExecution:
    if len(chunk) == 1:
        msg = "single-repo chunks must be classified by their captured transient failure"
        raise AssertionError(msg)
    mid = len(chunk) // 2
    attempts: list[tuple[tuple[_RepoTarget, ...], dict[str, Any] | HttpError]] = []
    any_success = False
    for half in (chunk[:mid], chunk[mid:]):
        try:
            attempts.append((half, _post_with_transient_retries(http, endpoint, query(half))))
            any_success = True
        except HttpError as exc:
            if not _is_transient_probe_error(exc):
                raise
            attempts.append((half, exc))
    if not any_success and no_progress_split_lookahead <= 0:
        return _ChunkExecution([], _transient_failures_from_attempts(attempts))

    payloads: list[tuple[tuple[_RepoTarget, ...], dict[str, Any]]] = []
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


def _transient_failures_from_attempts(
    attempts: list[tuple[tuple[_RepoTarget, ...], dict[str, Any] | HttpError]],
) -> list[BatchRepoRefsFailure]:
    failures: list[BatchRepoRefsFailure] = []
    for chunk, outcome in attempts:
        if isinstance(outcome, HttpError):
            failures.extend(_transient_failures(chunk, outcome))
    return failures


def _transient_failures(
    chunk: tuple[_RepoTarget, ...],
    exc: HttpError,
) -> list[BatchRepoRefsFailure]:
    return [_transient_failure(target, exc) for target in chunk]


def _transient_failure(target: _RepoTarget, exc: HttpError) -> BatchRepoRefsFailure:
    return BatchRepoRefsFailure(
        full_name=target.full_name,
        reason=str(exc),
        kind=_transient_failure_kind(exc),
        status_code=exc.status_code,
        url=exc.url,
    )


def _transient_failure_kind(exc: HttpError) -> BatchRepoRefsFailureKind:
    if _is_server_error(exc):
        return "server_error"
    return "transport"


def _post(http: HttpClient, endpoint: str, query: str) -> dict[str, Any]:
    try:
        payload = http.post_json(endpoint, json={"query": query}, retry=None)
    except HttpError as exc:
        if exc.status_code in _GRAPHQL_ACCESS_STATUSES:
            raise _graphql_http_error(exc) from exc
        raise
    if not isinstance(payload, dict):
        raise HttpError(
            f"expected JSON object from {endpoint}, got {type(payload).__name__}",
            url=endpoint,
        )
    return payload


def _build_batch_query(chunk: tuple[_RepoTarget, ...], kinds: tuple[RefKind, ...]) -> str:
    fields = " ".join(
        _repository_field(f"r{index}", target, kinds) for index, target in enumerate(chunk)
    )
    return f"{{ {fields} {_RATE_LIMIT_FIELD} }}"


def _build_default_branch_query(chunk: tuple[_RepoTarget, ...]) -> str:
    fields = " ".join(
        (
            f"r{index}: repository(owner: {json.dumps(target.owner)}, "
            f"name: {json.dumps(target.name)}) "
            f"{{ nameWithOwner defaultBranchRef {{ name target {{ oid }} }} }}"
        )
        for index, target in enumerate(chunk)
    )
    return f"{{ {fields} {_RATE_LIMIT_FIELD} }}"


def _repository_field(alias: str, target: _RepoTarget, kinds: tuple[RefKind, ...]) -> str:
    refs = " ".join(_refs_field(kind) for kind in kinds)
    return (
        f"{alias}: repository(owner: {json.dumps(target.owner)}, "
        f"name: {json.dumps(target.name)}) "
        f"{{ nameWithOwner defaultBranchRef {{ name }} {refs} }}"
    )


def _build_page_query(target: _RepoTarget, kind: RefKind, cursor: str) -> str:
    return (
        f"{{ r0: repository(owner: {json.dumps(target.owner)}, "
        f"name: {json.dumps(target.name)}) "
        f"{{ {_refs_field(kind, after=cursor)} }} {_RATE_LIMIT_FIELD} }}"
    )


def _refs_field(kind: RefKind, *, after: str | None = None) -> str:
    cursor = f", after: {json.dumps(after)}" if after is not None else ""
    return (
        f"{kind}: refs(refPrefix: {json.dumps(_REF_PREFIXES[kind])}, "
        f"first: {_PAGE_SIZE}{cursor}) "
        f"{{ pageInfo {{ hasNextPage endCursor }} nodes {{ name {_TARGET_FIELD} }} }}"
    )


def _resolve_chunk(
    http: HttpClient,
    endpoint: str,
    chunk: tuple[_RepoTarget, ...],
    kinds: tuple[RefKind, ...],
    payload: dict[str, Any],
) -> tuple[list[RepoRefs], list[str], list[BatchRepoRefsFailure], _RateLimit]:
    """Parse one chunk payload; issues follow-up POSTs for ref pagination."""
    missing_aliases = _classify_errors(payload)
    data = payload.get("data") or {}
    found: list[RepoRefs] = []
    missing: list[str] = []
    failures: list[BatchRepoRefsFailure] = []
    rate_limit = _rate_limit(payload)
    for index, target in enumerate(chunk):
        alias = f"r{index}"
        node = data.get(alias)
        if node is None:
            if alias not in missing_aliases:
                raise UntapedError(
                    f"github graphql returned null for {target.full_name} "
                    "without an explanatory error"
                )
            missing.append(target.full_name)
            continue
        repo_result, page_rate_limit = _resolve_repo(http, endpoint, target, node, kinds)
        rate_limit = _merge_rate_limit(rate_limit, page_rate_limit)
        if isinstance(repo_result, BatchRepoRefsFailure):
            failures.append(repo_result)
            continue
        found.append(repo_result)
    return found, missing, failures, rate_limit


def _resolve_default_branch_chunk(
    chunk: tuple[_RepoTarget, ...],
    payload: dict[str, Any],
) -> tuple[list[RepoRefs], list[str], _RateLimit]:
    """Parse one connection-free default-branch payload."""
    missing_aliases = _classify_errors(payload)
    data = payload.get("data") or {}
    found: list[RepoRefs] = []
    missing: list[str] = []
    rate_limit = _rate_limit(payload)
    for index, target in enumerate(chunk):
        alias = f"r{index}"
        node = data.get(alias)
        if node is None:
            if alias not in missing_aliases:
                raise UntapedError(
                    f"github graphql returned null for {target.full_name} "
                    "without an explanatory error"
                )
            missing.append(target.full_name)
            continue
        found.append(_resolve_default_branch_repo(target, node))
    return found, missing, rate_limit


def _classify_errors(payload: dict[str, Any]) -> set[str]:
    """Return aliases of missing repos; raise on any other GraphQL error."""
    missing: set[str] = set()
    for error in payload.get("errors") or ():
        if not isinstance(error, dict):
            raise _graphql_payload_error(error)
        alias = _repo_alias_from_path(error.get("path"))
        if error.get("type") in _MISSING_ERROR_TYPES and alias is not None:
            missing.add(alias)
            continue
        raise _graphql_payload_error(error)
    return missing


def _repo_alias_from_path(path: object) -> str | None:
    if not isinstance(path, list | tuple) or len(path) != 1:
        return None
    alias = path[0]
    if isinstance(alias, str) and alias.startswith("r") and alias[1:].isdigit():
        return alias
    return None


def _graphql_http_error(exc: HttpError) -> GithubGraphqlError:
    detail = _message_from_body(exc.body)
    kind = _classify_graphql_failure(status_code=exc.status_code, message=detail)
    return _github_graphql_error(
        kind,
        detail,
        status_code=exc.status_code,
        url=exc.url,
        body=exc.body,
    )


def _graphql_payload_error(error: object) -> GithubGraphqlError:
    if isinstance(error, dict):
        detail = _message_from_graphql_error(error)
        error_type = error.get("type")
        kind = _classify_graphql_failure(
            status_code=None,
            message=detail,
            error_type=str(error_type) if error_type is not None else None,
        )
    else:
        detail = repr(error)
        kind = "unknown"
    return _github_graphql_error(kind, detail)


def _message_from_graphql_error(error: dict[str, Any]) -> str | None:
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        return message
    return repr(error)


def _message_from_body(body: str | None) -> str | None:
    if body is None:
        return None
    stripped = body.strip()
    if not stripped:
        return None
    try:
        decoded = json.loads(stripped)
    except ValueError:
        return stripped
    if isinstance(decoded, dict):
        for key in ("message", "error", "detail"):
            message = decoded.get(key)
            if isinstance(message, str) and message.strip():
                return message.strip()
        errors = decoded.get("errors")
        if isinstance(errors, list):
            for item in errors:
                if isinstance(item, dict):
                    message = item.get("message")
                    if isinstance(message, str) and message.strip():
                        return message.strip()
    return stripped


def _classify_graphql_failure(
    *,
    status_code: int | None,
    message: str | None,
    error_type: str | None = None,
) -> GithubGraphqlErrorKind:
    haystack = f"{message or ''} {error_type or ''}".lower()
    if status_code == 401 or "bad credentials" in haystack or "requires authentication" in haystack:
        return "auth"
    if "secondary rate limit" in haystack or "abuse detection" in haystack:
        return "secondary_rate_limited"
    if (
        error_type == "RATE_LIMITED"
        or status_code == 429
        or "rate limit exceeded" in haystack
        or "api rate limit" in haystack
    ):
        return "rate_limited"
    if status_code == 403 or error_type == "FORBIDDEN" or "resource not accessible" in haystack:
        return "forbidden"
    return "unknown"


def _github_graphql_error(
    kind: GithubGraphqlErrorKind,
    detail: str | None,
    *,
    status_code: int | None = None,
    url: str | None = None,
    body: str | None = None,
) -> GithubGraphqlError:
    prefixes = {
        "rate_limited": "github graphql rate limit exceeded",
        "secondary_rate_limited": "github graphql secondary rate limit exceeded",
        "auth": "github graphql authentication failed",
        "forbidden": "github graphql access forbidden",
        "unknown": "github graphql error",
    }
    fallback = f"HTTP {status_code}" if status_code is not None else "unknown failure"
    if url and status_code is not None:
        fallback = f"{fallback} for {url}"
    message = f"{prefixes[kind]}: {detail or fallback}"
    return GithubGraphqlError(
        message,
        kind=kind,
        status_code=status_code,
        url=url,
        body=body,
    )


def _resolve_repo(
    http: HttpClient,
    endpoint: str,
    target: _RepoTarget,
    node: dict[str, Any],
    kinds: tuple[RefKind, ...],
) -> tuple[RepoRefs | BatchRepoRefsFailure, _RateLimit]:
    """Build one :class:`RepoRefs`, following ref-pagination cursors.

    Repos with >100 refs in a namespace are rare; follow-up single-repo
    queries run serially with ``after: <cursor>`` until exhausted.
    """
    rate_limit = _RateLimit()
    refs: list[RepoRef] = []
    for kind in kinds:
        connection = node[kind]
        while True:
            refs.extend(_parse_ref_nodes(kind, connection["nodes"]))
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            try:
                payload = _post_with_transient_retries(
                    http,
                    endpoint,
                    _build_page_query(target, kind, page["endCursor"]),
                )
            except HttpError as exc:
                if not _is_transient_probe_error(exc):
                    raise
                return _transient_failure(target, exc), rate_limit
            # The repo resolved in the batch query, so even NOT_FOUND
            # here (deleted mid-probe) is unexpected enough to raise.
            if _classify_errors(payload):
                raise UntapedError(
                    f"github graphql lost access to {target.full_name} during ref pagination"
                )
            rate_limit = _merge_rate_limit(rate_limit, _rate_limit(payload))
            connection = payload["data"]["r0"][kind]
    default_branch_ref = node.get("defaultBranchRef")
    default_branch = default_branch_ref["name"] if isinstance(default_branch_ref, dict) else None
    return (
        RepoRefs(full_name=target.full_name, default_branch=default_branch, refs=tuple(refs)),
        rate_limit,
    )


def _resolve_default_branch_repo(target: _RepoTarget, node: dict[str, Any]) -> RepoRefs:
    default_branch_ref = node.get("defaultBranchRef")
    if not isinstance(default_branch_ref, dict):
        return RepoRefs(full_name=target.full_name, default_branch=None, refs=())
    name = default_branch_ref.get("name")
    branch_name = name if isinstance(name, str) and name else None
    target_node = default_branch_ref.get("target")
    refs = (
        (RepoRef(kind="heads", name=branch_name, sha=_peel_oid(target_node)),)
        if branch_name is not None and isinstance(target_node, dict)
        else ()
    )
    return RepoRefs(full_name=target.full_name, default_branch=branch_name, refs=refs)


def _parse_ref_nodes(kind: RefKind, nodes: list[dict[str, Any]]) -> list[RepoRef]:
    # GitHub's schema allows ``Ref.target`` to be null; a ref without a
    # resolvable object is useless to the freshness probe, so skip it.
    return [
        RepoRef(kind=kind, name=node["name"], sha=_peel_oid(node["target"]))
        for node in nodes
        if node.get("target") is not None
    ]


def _peel_oid(target: dict[str, Any]) -> str:
    """Return the deepest oid: annotated tags nest the commit under ``target``."""
    oid = target.get("oid", "")
    inner = target.get("target")
    while isinstance(inner, dict):
        if inner.get("oid"):
            oid = inner["oid"]
        inner = inner.get("target")
    return str(oid)


def _rate_limit(payload: dict[str, Any]) -> _RateLimit:
    data = payload.get("data")
    if not isinstance(data, dict):
        return _RateLimit()
    rate_limit = data.get("rateLimit")
    if not isinstance(rate_limit, dict):
        return _RateLimit()
    cost = rate_limit.get("cost")
    remaining = rate_limit.get("remaining")
    reset_at = _parse_reset_at(rate_limit.get("resetAt"))
    return _RateLimit(
        cost=cost if isinstance(cost, int) else None,
        remaining=remaining if isinstance(remaining, int) else None,
        reset_at=reset_at,
    )


def _parse_reset_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _merge_rate_limit(current: _RateLimit, latest: _RateLimit) -> _RateLimit:
    return _RateLimit(
        cost=_sum_optional_ints(current.cost, latest.cost),
        remaining=latest.remaining if latest.remaining is not None else current.remaining,
        reset_at=latest.reset_at if latest.reset_at is not None else current.reset_at,
    )


def _sum_optional_ints(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right
