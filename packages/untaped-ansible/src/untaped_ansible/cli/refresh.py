"""Shared source-refresh wiring for the source and graph CLI commands."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterable
from typing import Literal

from untaped.sdk import NO_PROGRESS, Diagnostics, HttpSettings, ProgressHandle, plural
from untaped_ansible.application.refresh_git_index import (
    RefreshGitSourceIndex,
    RefreshResult,
)
from untaped_ansible.domain.payloads import (
    GRAPHQL_RATE_LIMIT_FALLBACK,
    GRAPHQL_TRANSIENT_FALLBACK,
    SkippedDependencyFile,
)
from untaped_ansible.infrastructure import (
    AutoRefProbe,
    GithubRefProbe,
    GitRemoteRefProbe,
    GitSourceStore,
    SqliteDependencyIndex,
)
from untaped_ansible.settings import AnsibleSettings, SourceDefinition
from untaped_github.api import GithubClient, GithubSettings, github_web_host

GIT_PARALLEL_CAP = 32
"""Upper bound for ``--parallel`` Git fetches (matches ``ansible.git_fetch_parallel``)."""

_diag = Diagnostics()


def run_source_refresh(
    source: SourceDefinition,
    *,
    source_key: str,
    action: str,
    label: str,
    index: SqliteDependencyIndex,
    aliases: dict[str, str],
    settings: AnsibleSettings,
    github_settings: GithubSettings,
    http: HttpSettings,
    concurrency: int,
    backend: Literal["auto", "graphql", "git"] | None = None,
) -> RefreshResult:
    """Refresh one source with stderr progress, then print its summary and warnings."""
    started_at = time.perf_counter()
    with _diag.progress(f"Refreshing {label}") as progress:
        result = refresh_source(
            source,
            source_key=source_key,
            index=index,
            aliases=aliases,
            settings=settings,
            github_settings=github_settings,
            http=http,
            concurrency=concurrency,
            backend=backend,
            progress=progress,
        )
    _diag.info(
        refresh_summary(
            action,
            label,
            result,
            concurrency=concurrency,
            elapsed=time.perf_counter() - started_at,
        ),
    )
    warn_low_rate_limit(result, threshold=settings.source_refresh_rate_limit_floor)
    warn_skipped_files(result)
    warn_ignored_collections(result.ignored_collections)
    warn_probe_fallbacks(result)
    return result


def refresh_source(
    source: SourceDefinition,
    *,
    source_key: str,
    index: SqliteDependencyIndex,
    aliases: dict[str, str],
    settings: AnsibleSettings,
    github_settings: GithubSettings,
    http: HttpSettings,
    concurrency: int,
    backend: Literal["auto", "graphql", "git"] | None = None,
    progress: ProgressHandle = NO_PROGRESS,
) -> RefreshResult:
    """Run a git-backed source refresh with fully wired adapters."""
    # The forge decides how its URLs look; the git plugin's GitHost supplies its token.
    protocol = github_settings.git_protocol
    with GithubClient(github_settings, http=http) as github:
        git = GitSourceStore()
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
            github_host=github_web_host(github_settings.base_url),
        )(source, source_key=source_key)
    return result


def refresh_summary(
    action: str,
    label: str,
    result: RefreshResult,
    *,
    concurrency: int,
    elapsed: float,
) -> str:
    """One-line stderr summary for a completed source refresh."""
    message = (
        f"{action} {label}: {plural(result.repos, 'repo')}, {plural(result.refs, 'ref')}, "
        f"{plural(result.edges, 'edge')}, "
        f"{result.changed_refs} changed, {result.unchanged_refs} unchanged in {elapsed:.2f}s"
    )
    return f"{message} (parallel {concurrency})"


def warn_low_rate_limit(result: RefreshResult, *, threshold: int) -> None:
    """Warn on stderr when the GraphQL rate limit budget is running out."""
    remaining = result.rate_limit_remaining
    if remaining is not None and remaining < threshold:
        _diag.warning(f"GitHub GraphQL rate limit is low: {remaining} points remaining")


def warn_probe_fallbacks(result: RefreshResult) -> None:
    """Warn when GraphQL probing fell back to per-repo Git ls-remote calls."""
    if not result.probe_fallbacks:
        return
    counts = Counter(result.probe_fallbacks.values())
    rate_limited = counts.pop(GRAPHQL_RATE_LIMIT_FALLBACK, 0)
    if rate_limited:
        _diag.warning(
            f"{plural(rate_limited, 'repo')} fell back to git ls-remote after "
            "GitHub GraphQL rate limit exhaustion; large fallbacks can be much slower "
            "because Git probing runs one network subprocess per repo",
        )
    transient = counts.pop(GRAPHQL_TRANSIENT_FALLBACK, 0)
    if transient:
        _diag.warning(
            f"{plural(transient, 'repo')} fell back to git ls-remote after transient "
            "GitHub GraphQL probe failures",
        )
    unknown = sum(counts.values())
    if unknown:
        reasons = ", ".join(f"{reason} ({count})" for reason, count in sorted(counts.items()))
        _diag.warning(
            f"{plural(unknown, 'repo')} fell back to git ls-remote for "
            f"{plural(len(counts), 'unrecognized fallback reason')}: {reasons}",
        )


def warn_ignored_collections(names: Iterable[str]) -> None:
    """Warn once that requirements-file collections are not graphed."""
    message = ignored_collections_warning(names)
    if message is not None:
        _diag.warning(message)


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


def warn_skipped_files(result: RefreshResult) -> None:
    """Warn about refs the store refused and dependency files skipped during parsing."""
    for ref in result.skipped_refs:
        noun = "branch" if ref.kind == "heads" else "tag"
        _diag.warning(f"skipped {noun} {ref.repo}@{ref.ref}: {ref.reason}")
    for skipped in result.skipped_files:
        _diag.warning(format_skipped_dependency_file(skipped))


def format_skipped_dependency_file(skipped: SkippedDependencyFile) -> str:
    """Render a skipped dependency file warning body."""
    ref = f"@{skipped.ref}" if skipped.ref is not None else ""
    return f"skipped {skipped.repo}{ref} {skipped.source_path}: {skipped.reason}"
