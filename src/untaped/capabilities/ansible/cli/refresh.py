"""Shared source-refresh wiring for the source and graph CLI commands."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Literal

from untaped.capabilities.ansible.application.refresh_git_index import RefreshGitSourceIndex
from untaped.capabilities.ansible.application.refresh_index import RefreshResult
from untaped.capabilities.ansible.domain.identity import github_web_host
from untaped.capabilities.ansible.domain.payloads import (
    GRAPHQL_RATE_LIMIT_FALLBACK,
    GRAPHQL_TRANSIENT_FALLBACK,
    RefreshProgressEvent,
    SkippedDependencyFile,
)
from untaped.capabilities.ansible.infrastructure import (
    AutoRefProbe,
    GithubRefProbe,
    GitRemoteRefProbe,
    GitRepositoryCache,
    SqliteDependencyIndex,
)
from untaped.capabilities.ansible.settings import AnsibleSettings, SourceDefinition
from untaped.capabilities.github.ansible import GithubClient, GithubSettings
from untaped.capability_api import HttpSettings, ProgressHandle, UiContext, git_auth_header, plural

GIT_PARALLEL_CAP = 32
"""Upper bound for ``--parallel`` Git fetches (matches ``ansible.git_fetch_concurrency``)."""


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
    ui: UiContext,
    backend: Literal["auto", "graphql", "git"] | None = None,
) -> RefreshResult:
    """Refresh one source with stderr progress, then echo summary and warnings."""
    started_at = time.perf_counter()
    with ui.progress(f"Refreshing {label}") as progress:
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
            on_progress=progress_reporter(progress),
        )
    ui.message(
        "info",
        refresh_summary(
            action,
            label,
            result,
            concurrency=concurrency,
            elapsed=time.perf_counter() - started_at,
        ),
    )
    warn_low_rate_limit(result, threshold=settings.source_refresh_rate_limit_floor, ui=ui)
    warn_skipped_files(result, ui=ui)
    warn_ignored_collections(result.ignored_collections, ui=ui)
    warn_probe_fallbacks(result, ui=ui)
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
    on_progress: Callable[[RefreshProgressEvent], None] | None = None,
) -> RefreshResult:
    """Run a git-backed source refresh with fully wired adapters."""
    with GithubClient(github_settings, http=http) as github:
        token = (
            github_settings.token.get_secret_value().strip()
            if github_settings.token is not None
            else ""
        )
        git = GitRepositoryCache()
        selected_backend = backend or settings.source_refresh_backend
        auth_header = git_auth_header(token) if token else None
        graphql_probe = GithubRefProbe(github, concurrency=settings.probe_concurrency)
        git_probe = GitRemoteRefProbe(
            git,
            clone_protocol=settings.git_clone_protocol,
            auth_header=auth_header,
            concurrency=settings.probe_concurrency,
        )
        result = RefreshGitSourceIndex(
            github=github,
            git=git,
            probe=AutoRefProbe(graphql_probe, git_probe, backend=selected_backend),
            index=index,
            aliases=aliases,
            default_dependency_paths=settings.dependency_paths,
            repo_cache_path=settings.repo_cache_path,
            clone_protocol=settings.git_clone_protocol,
            fetch_depth=settings.git_fetch_depth,
            blob_filter=settings.git_blob_filter,
            auth_header=auth_header,
            concurrency=concurrency,
            ref_scan_default=settings.ref_scan_default,
            repo_batch_size=settings.source_refresh_repo_batch_size,
            rate_limit_floor=settings.source_refresh_rate_limit_floor,
            on_progress=on_progress,
            github_host=github_web_host(github_settings.base_url),
        )(source, source_key=source_key)
    return result


def progress_reporter(handle: ProgressHandle) -> Callable[[RefreshProgressEvent], None]:
    """Adapt refresh progress events onto a core UiContext progress handle."""
    last_phase: str | None = None

    def report(event: RefreshProgressEvent) -> None:
        nonlocal last_phase
        new_phase = event.phase != last_phase
        last_phase = event.phase
        fraction = event.done / event.total if event.total else None
        handle.update(_format_progress(event), fraction=fraction, new_phase=new_phase)

    return report


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


def warn_low_rate_limit(result: RefreshResult, *, threshold: int, ui: UiContext) -> None:
    """Warn on stderr when the GraphQL rate limit budget is running out."""
    remaining = result.rate_limit_remaining
    if remaining is not None and remaining < threshold:
        ui.message("warning", f"GitHub GraphQL rate limit is low: {remaining} points remaining")


def warn_probe_fallbacks(result: RefreshResult, *, ui: UiContext) -> None:
    """Warn when GraphQL probing fell back to per-repo Git ls-remote calls."""
    if not result.probe_fallbacks:
        return
    counts = Counter(result.probe_fallbacks.values())
    rate_limited = counts.pop(GRAPHQL_RATE_LIMIT_FALLBACK, 0)
    if rate_limited:
        ui.message(
            "warning",
            f"{plural(rate_limited, 'repo')} fell back to git ls-remote after "
            "GitHub GraphQL rate limit exhaustion; large fallbacks can be much slower "
            "because Git probing runs one network subprocess per repo",
        )
    transient = counts.pop(GRAPHQL_TRANSIENT_FALLBACK, 0)
    if transient:
        ui.message(
            "warning",
            f"{plural(transient, 'repo')} fell back to git ls-remote after transient "
            "GitHub GraphQL probe failures",
        )
    unknown = sum(counts.values())
    if unknown:
        reasons = ", ".join(f"{reason} ({count})" for reason, count in sorted(counts.items()))
        ui.message(
            "warning",
            f"{plural(unknown, 'repo')} fell back to git ls-remote for "
            f"{plural(len(counts), 'unrecognized fallback reason')}: {reasons}",
        )


def warn_ignored_collections(names: Iterable[str], *, ui: UiContext) -> None:
    """Warn once that requirements-file collections are not graphed."""
    message = ignored_collections_warning(names)
    if message is not None:
        ui.message("warning", message)


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


def warn_deprecated_settings(settings: AnsibleSettings, *, ui: UiContext) -> None:
    """Warn about profile keys kept only for config compatibility."""
    if settings.freshness_ttl is not None:
        ui.message(
            "warning",
            "ansible.freshness_ttl is deprecated and ignored; pass --refresh or run "
            "`untaped ansible source refresh NAME` to check remote data",
        )


def warn_skipped_files(result: RefreshResult, *, ui: UiContext) -> None:
    """Warn when dependency files were skipped during parsing."""
    for skipped in result.skipped_files:
        ui.message("warning", format_skipped_dependency_file(skipped))


def format_skipped_dependency_file(skipped: SkippedDependencyFile) -> str:
    """Render a skipped dependency file warning body."""
    ref = f"@{skipped.ref}" if skipped.ref is not None else ""
    return f"skipped {skipped.repo}{ref} {skipped.source_path}: {skipped.reason}"


def _format_progress(event: RefreshProgressEvent) -> str:
    if event.phase == "expanding":
        return f"expanding source: {event.done}/{event.total} selectors"
    noun = "probing refs" if event.phase == "probing" else "fetching changes"
    message = f"{noun}: {event.done}/{event.total} repos"
    if event.changed is not None:
        message = f"{message}, {event.changed} changed"
    return message
