"""Shared source-refresh wiring: the source and graph commands read the index through it.

:func:`serve_sources` serves the selected sources through :data:`SOURCE_DATA`, so a
source past its max age is refreshed before the command reads it, and a
refresh that fails leaves the cached data served with one ``untaped.stale``
line. The use case knows nothing of the cache: :func:`_filler` turns its
result into a :class:`~untaped.sdk.Filled` (or an error) here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from untaped.sdk import (
    NO_PROGRESS,
    Failure,
    HttpFactory,
    PluginHandle,
    ProgressHandle,
    Served,
    UntapedError,
    command_line,
    most_severe,
    plural,
)
from untaped_ansible.application.refresh_git_index import (
    RefreshGitSourceIndex,
    RefreshResult,
)
from untaped_ansible.domain.payloads import (
    GRAPHQL_RATE_LIMIT_FALLBACK,
    GRAPHQL_TRANSIENT_FALLBACK,
    SkippedDependencyFile,
)
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import (
    AutoRefProbe,
    GithubRefProbe,
    GitRemoteRefProbe,
    GitSourceStore,
    SqliteDependencyIndex,
)
from untaped_ansible.settings import SOURCE_DATA, AnsibleSettings, SourceDefinition
from untaped_github.api import GithubClient, GithubSettings, github_web_host

GIT_PARALLEL_CAP = 32
"""Upper bound for ``--parallel`` Git fetches (matches ``ansible.git_fetch_parallel``)."""

#: Failed repos a partial refresh names before ``and N more``.
_MAX_LISTED_REPOS = 10

Backend = Literal["auto", "graphql", "git"]


@dataclass(frozen=True)
class SelectedSource:
    """One source a command reads: its definition, its index key and how a human names it.

    ``key`` is the index's own (``source:prod``, ``inline:<hash>``);
    ``label`` names it in a tree header and warnings (``prod``); ``subject``
    in the refresh task and the stale line (``source prod``). An inline
    source's label and subject are both its selector flags.
    """

    definition: SourceDefinition
    key: str
    label: str
    subject: str


@dataclass(frozen=True)
class RefreshWiring:
    """What a refresh of any selected source needs, resolved once per command."""

    index: SqliteDependencyIndex
    aliases: dict[str, str]
    settings: AnsibleSettings
    github_settings: GithubSettings
    http: HttpFactory
    plugin: PluginHandle
    warn: Callable[[str], None]
    """Where the refresh's warnings go: the command's ``diag.warning``."""
    concurrency: int
    backend: Backend | None = None


@dataclass
class RefreshRecord:
    """What each refresh a command ran returned or raised, by key."""

    results: dict[str, RefreshResult] = field(default_factory=dict)
    errors: dict[str, UntapedError] = field(default_factory=dict)


def serve_sources[T](
    selections: Sequence[SelectedSource],
    *,
    refresh: bool | None,
    read: Callable[[], T],
    wiring: RefreshWiring,
    empty: Callable[[T], bool] | None = None,
    record: RefreshRecord | None = None,
    warnings: bool = True,
) -> Served[T, RefreshResult]:
    """Refresh what ``refresh`` asks for of ``selections``, then answer ``read()``.

    Each key's freshness is first brought in line with the index
    (:func:`sync_freshness`). The refresh's warnings (rate limit, skipped
    refs and files, ignored collections, probe fallbacks) print once per
    refresh, failed ones included, unless ``warnings`` is false; ``record``
    collects what each refresh returned or raised, for a caller that reports
    more (and prints the warnings itself, with :func:`warn_refresh`).
    """
    for selection in selections:
        sync_freshness(selection.key, wiring.index)
    record = RefreshRecord() if record is None else record
    by_key = {selection.key: selection for selection in selections}
    try:
        return SOURCE_DATA.serve(
            {selection.key: selection.subject for selection in selections},
            refresh=refresh,
            fill=_filler(by_key, wiring, record),
            read=read,
            empty=empty,
        )
    finally:
        if warnings:
            for result in record.results.values():
                warn_refresh(result, wiring.settings, wiring.warn)


def sync_freshness(key: str, index: SqliteDependencyIndex) -> None:
    """Bring what :data:`SOURCE_DATA` knows of ``key`` in line with the index's own rows.

    The index's row says only whether a completed refresh left data for
    ``key``, never how fresh it is: a row with nothing known of it is the
    data of a ``source refresh`` from before the cache (seeded with its
    ``scanned_at``), and a key known as refreshed with no row lost its data
    (a redefined or removed source, a rebuilt or moved index), so its next
    read refreshes it.
    """
    row = index.status(key)
    if row is not None:
        if SOURCE_DATA.freshness(key).refreshed_at is None:
            SOURCE_DATA.seed(key, refreshed_at=row.scanned_at)
    elif SOURCE_DATA.freshness(key).refreshed_at is not None:
        SOURCE_DATA.forget(key)


def _filler(
    selections: Mapping[str, SelectedSource],
    wiring: RefreshWiring,
    record: RefreshRecord,
) -> Callable[[str, ProgressHandle], RefreshResult]:
    """The ``fill`` that refreshes one selected source into the index."""

    def fill(key: str, progress: ProgressHandle) -> RefreshResult:
        try:
            return _fill(key, progress)
        except UntapedError as exc:
            record.errors[key] = exc
            raise

    def _fill(key: str, progress: ProgressHandle) -> RefreshResult:
        result = record.results[key] = refresh_source(
            selections[key].definition,
            source_key=key,
            index=wiring.index,
            aliases=wiring.aliases,
            settings=wiring.settings,
            github_settings=wiring.github_settings,
            http=wiring.http,
            plugin=wiring.plugin,
            concurrency=wiring.concurrency,
            backend=wiring.backend,
            progress=progress,
        )
        error = _fill_error(result)
        if error is not None:
            raise error
        return result.model_copy(
            update={
                "partial": _partial_failure(result),
                "retry_at": _retry_at(result, wiring.settings),
                "summary": fill_summary(result),
            }
        )

    return fill


def _fill_error(result: RefreshResult) -> AnsibleError | None:
    """Why ``result`` saved nothing new (a pause, every repo failed), else ``None``."""
    if not result.completed:
        return _pause_error(result)
    if result.repos and len(result.failures) == result.repos:
        return _all_failed_error(result)
    return None


def _pause_error(result: RefreshResult) -> AnsibleError:
    """A refresh paused at the GraphQL rate-limit floor; resuming skips the finished repos."""
    reset = result.rate_limit_reset_at
    return AnsibleError(
        result.pause_reason or "source refresh paused before completion",
        category="unavailable",
        system="github",
        hint="rerun to resume; finished repos are skipped",
        details={} if reset is None else {"retry_at": reset.isoformat()},
    )


def repo_errors(result: RefreshResult) -> list[AnsibleError]:
    """Each repo the refresh failed on, as the error its category exits with."""
    return [AnsibleError(failure.reason, category=failure.category) for failure in result.failures]


def _all_failed_error(result: RefreshResult) -> AnsibleError:
    """Every repo of the source failed: the index was left as it was."""
    return AnsibleError(
        f"refresh failed for all {plural(result.repos, 'repo')}; index left unchanged",
        category=most_severe(repo_errors(result)).category,
        details={"repos": [failure.repo for failure in result.failures]},
    )


def _partial_failure(result: RefreshResult) -> Failure | None:
    """What a completed refresh left out, as one failure naming the repos; ``None`` if nothing."""
    if not result.failures:
        return None
    repos = [failure.repo for failure in result.failures]
    shown = ", ".join(repos[:_MAX_LISTED_REPOS])
    more = len(repos) - _MAX_LISTED_REPOS
    names = f"{shown}, and {more} more" if more > 0 else shown
    error = AnsibleError(
        f"{len(repos)} of {plural(result.repos, 'repo')} failed to refresh ({names})",
        category=most_severe(repo_errors(result)).category,
        details={"repos": repos},
    )
    return Failure.from_exception(error)


def _retry_at(result: RefreshResult, settings: AnsibleSettings) -> datetime | None:
    """GitHub's reset time when the refresh spent the budget below the floor, else ``None``.

    Every GraphQL answer carries a reset time; only a spent budget makes it
    the time to come back.
    """
    remaining = result.rate_limit_remaining
    if remaining is None or remaining >= settings.source_refresh_rate_limit_floor:
        return None
    return result.rate_limit_reset_at


def fill_summary(result: RefreshResult) -> str:
    """What a completed refresh did: ``780 repos, 2141 refs, 9804 edges, 12 changed, …``."""
    return (
        f"{plural(result.repos, 'repo')}, {plural(result.refs, 'ref')}, "
        f"{plural(result.edges, 'edge')}, "
        f"{result.changed_refs} changed, {result.unchanged_refs} unchanged"
    )


def warn_refresh(
    result: RefreshResult, settings: AnsibleSettings, warn: Callable[[str], None]
) -> None:
    """The refresh's warning groups: rate limit, skipped refs and files, collections, probes."""
    remaining = result.rate_limit_remaining
    if remaining is not None and remaining < settings.source_refresh_rate_limit_floor:
        warn(f"GitHub GraphQL rate limit is low: {remaining} points remaining")
    _warn_skipped_files(result, warn)
    message = ignored_collections_warning(result.ignored_collections)
    if message is not None:
        warn(message)
    _warn_probe_fallbacks(result, warn)


def refresh_source(
    source: SourceDefinition,
    *,
    source_key: str,
    index: SqliteDependencyIndex,
    aliases: dict[str, str],
    settings: AnsibleSettings,
    github_settings: GithubSettings,
    http: HttpFactory,
    plugin: PluginHandle,
    concurrency: int,
    backend: Backend | None = None,
    progress: ProgressHandle = NO_PROGRESS,
) -> RefreshResult:
    """Run a git-backed source refresh with fully wired adapters."""
    # The forge decides how its URLs look; the git plugin's GitHost supplies its token.
    protocol = github_settings.git_protocol
    with GithubClient(github_settings, http=http) as github:
        git = GitSourceStore(plugin=plugin)
        selected_backend = backend or settings.source_refresh_backend
        graphql_probe = GithubRefProbe(github, concurrency=settings.probe_parallel)
        git_probe = GitRemoteRefProbe(
            git, clone_protocol=protocol, concurrency=settings.probe_parallel
        )
        result = RefreshGitSourceIndex(
            github=github,
            git=git,
            probe=AutoRefProbe(graphql_probe, git_probe, backend=selected_backend),
            index=index,
            aliases=aliases,
            default_dependency_paths=settings.dependency_paths,
            clone_protocol=protocol,
            concurrency=concurrency,
            ref_scan_default=settings.ref_scan_default,
            repo_batch_size=settings.source_refresh_repo_batch_size,
            rate_limit_floor=settings.source_refresh_rate_limit_floor,
            progress=progress,
            github_host=github_web_host(github_settings.api_url),
        )(source, source_key=source_key)
    return result


def _warn_probe_fallbacks(result: RefreshResult, warn: Callable[[str], None]) -> None:
    """Warn when GraphQL probing fell back to per-repo Git ls-remote calls."""
    if not result.probe_fallbacks:
        return
    counts = Counter(result.probe_fallbacks.values())
    rate_limited = counts.pop(GRAPHQL_RATE_LIMIT_FALLBACK, 0)
    if rate_limited:
        warn(
            f"{plural(rate_limited, 'repo')} fell back to git ls-remote after "
            "GitHub GraphQL rate limit exhaustion; large fallbacks can be much slower "
            "because Git probing runs one network subprocess per repo",
        )
    transient = counts.pop(GRAPHQL_TRANSIENT_FALLBACK, 0)
    if transient:
        warn(
            f"{plural(transient, 'repo')} fell back to git ls-remote after transient "
            "GitHub GraphQL probe failures",
        )
    unknown = sum(counts.values())
    if unknown:
        reasons = ", ".join(f"{reason} ({count})" for reason, count in sorted(counts.items()))
        warn(
            f"{plural(unknown, 'repo')} fell back to git ls-remote for "
            f"{plural(len(counts), 'unrecognized fallback reason')}: {reasons}",
        )


def ignored_collections_warning(names: Iterable[str]) -> str | None:
    """One-line summary of collections skipped because only roles are graphed."""
    unique = sorted(set(names))
    if not unique:
        return None
    shown = ", ".join(unique[:_MAX_LISTED_COLLECTIONS])
    extra = len(unique) - _MAX_LISTED_COLLECTIONS
    more = f", and {extra} more" if extra > 0 else ""
    verb = "was" if len(unique) == 1 else "were"
    return (
        f"{plural(len(unique), 'collection')} in requirements files {verb} ignored "
        f"(only roles are graphed): {shown}{more}"
    )


_MAX_LISTED_COLLECTIONS = 10


def _warn_skipped_files(result: RefreshResult, warn: Callable[[str], None]) -> None:
    """Warn about refs the store refused and files skipped during parsing or analysis.

    Each dependency file skip is a line; the variable analysis's are one
    count, since ``impact uses`` names those a role's dependents hold.
    """
    for ref in result.skipped_refs:
        noun = "branch" if ref.kind == "heads" else "tag"
        warn(f"skipped {noun} {ref.repo}@{ref.ref}: {ref.reason}")
    analysis = 0
    for skipped in result.skipped_files:
        if skipped.analysis:
            analysis += 1
        else:
            warn(format_skipped_dependency_file(skipped))
    if analysis:
        from untaped_ansible.commands.graph import Uses  # noqa: PLC0415 - import cycle

        warn(
            f"could not fully read {plural(analysis, 'file')} for variables; "
            f"`{command_line(Uses.spelled('ROLE', 'NAME'))}` names those in the role's dependents"
        )


def format_skipped_dependency_file(skipped: SkippedDependencyFile) -> str:
    """Render a skipped dependency file warning body."""
    ref = f"@{skipped.ref}" if skipped.ref is not None else ""
    return f"skipped {skipped.repo}{ref} {skipped.source_path}: {skipped.reason}"
