"""Use case for refreshing a dependency source index from the git plugin's repo store."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import batched
from typing import Literal

from pydantic import ConfigDict, Field

from untaped.sdk import UntapedError, bounded_map
from untaped.sdk.storage import NO_PROGRESS, Filled, ProgressHandle
from untaped_ansible.application.ports import (
    GitCache,
    IncrementalDependencyIndexWriter,
    ProbeRefs,
)
from untaped_ansible.application.source_refs import (
    RefScanDefault,
    pattern_matches,
    source_ref_selections,
)
from untaped_ansible.domain.identity import IdentityResolver, url_hosts
from untaped_ansible.domain.models import ParseReport, ParseWarning
from untaped_ansible.domain.parser import parse_dependency_file
from untaped_ansible.domain.payloads import (
    GitRef,
    IndexedDependency,
    ProbedRepo,
    ProbeTarget,
    RefScan,
    RefScanMetadata,
    RefScanTouch,
    RepoFailure,
    SkippedDependencyFile,
    SkippedRef,
    SourceRepoMetadata,
)
from untaped_ansible.domain.variable_models import AnalysedFile, CommitListing
from untaped_ansible.settings import analyser_version, parser_version
from untaped_ansible.sources import SourceDefinition
from untaped_git.api import HostBudget, Repo, Selection, probe_refs

ProbeMode = Literal["all", "default_branch"]

#: The repos a source selects, asked of its plugin (``select_repos`` in production).
SelectRepos = Callable[[SourceDefinition], Selection]


class RefreshResult(Filled):
    """Summary of an index refresh.

    What it inherits from :class:`~untaped.sdk.Filled` (``partial``,
    ``retry_at``, ``summary``) stays unset here: the CLI's adapter fills it
    in, so this use case words nothing for a human.
    """

    model_config = ConfigDict(frozen=True)

    source_key: str
    completed: bool = True
    pause_reason: str | None = None
    repos: int
    refs: int
    edges: int
    ignored_collections: tuple[str, ...] = ()
    changed_refs: int = 0
    unchanged_refs: int = 0
    failures: tuple[RepoFailure, ...] = ()
    skipped_files: tuple[SkippedDependencyFile, ...] = ()
    skipped_refs: tuple[SkippedRef, ...] = ()
    probe_fallbacks: dict[str, str] = Field(default_factory=dict)
    """Each repo read with ``git ls-remote`` although its host has a probe, and why."""
    budgets: tuple[HostBudget, ...] = ()
    """Each probing host's budget after the refresh's last probe of it."""
    selection_note: str | None = None
    """Why the source's plugin listed its repos rather than reading its selector back."""


@dataclass(frozen=True)
class _RepoRefreshResult:
    selected: frozenset[tuple[str, str, str]]
    scans: tuple[RefScan, ...]
    touches: tuple[RefScanTouch, ...]
    repo_metadata: SourceRepoMetadata
    ignored_collections: frozenset[str]
    skipped_files: tuple[SkippedDependencyFile, ...]
    analysed: tuple[AnalysedFile, ...] = ()
    listings: tuple[CommitListing, ...] = ()


@dataclass
class _Probed:
    """What the probes of a refresh read so far: refs, failures, fallbacks, budgets."""

    repos: dict[str, ProbedRepo] = field(default_factory=dict)
    failures: dict[str, RepoFailure] = field(default_factory=dict)
    fallbacks: dict[str, str] = field(default_factory=dict)
    budgets: dict[tuple[str, str], HostBudget] = field(default_factory=dict)

    def spent(self) -> list[HostBudget]:
        """The hosts whose budget is spent: the refresh asks them nothing more."""
        return [budget for budget in self.budgets.values() if budget.exhausted]


@dataclass(frozen=True)
class _RepoRefreshTask:
    repo: ProbeTarget
    default_branch: str
    refs: tuple[GitRef, ...]
    skipped: tuple[SkippedRef, ...] = ()


@dataclass(frozen=True)
class _ParsedDependencyFiles:
    reports: tuple[tuple[str, ParseReport], ...]
    ignored_collections: frozenset[str]
    warnings: tuple[ParseWarning, ...]


class RefreshGitSourceIndex:
    """Refresh dependency source data through Git transport and local objects."""

    def __init__(
        self,
        *,
        select: SelectRepos,
        git: GitCache,
        index: IncrementalDependencyIndexWriter,
        aliases: dict[str, str],
        default_dependency_paths: list[str],
        ref_scan_default: RefScanDefault = "all",
        concurrency: int = 8,
        repo_batch_size: int = 100,
        progress: ProgressHandle = NO_PROGRESS,
        probe: ProbeRefs = probe_refs,
    ) -> None:
        if concurrency < 1 or concurrency > 32:
            raise ValueError("concurrency must be between 1 and 32")
        if repo_batch_size < 1:
            raise ValueError("repo_batch_size must be at least 1")
        self._select = select
        self._git = git
        self._probe = probe
        self._index = index
        self._aliases = aliases
        self._default_dependency_paths = default_dependency_paths
        self._ref_scan_default = ref_scan_default
        self._concurrency = concurrency
        self._repo_batch_size = repo_batch_size
        self._progress = progress

    def __call__(self, source: SourceDefinition, *, source_key: str) -> RefreshResult:
        repos, note = self._expand_repos(source)
        # A URL names a repository on a host any source's repositories live on.
        hosts = self._index.repo_hosts() | url_hosts(repo.url for repo in repos)
        resolver = IdentityResolver(self._aliases, hosts=hosts)
        paths = source.dependency_paths or self._default_dependency_paths
        paths_fingerprint = _dependency_paths_fingerprint(paths)
        aliases_fingerprint = _aliases_fingerprint(self._aliases, hosts)
        source_fingerprint = source_refresh_fingerprint(
            source,
            repos=repos,
            paths_fingerprint=paths_fingerprint,
            aliases_fingerprint=aliases_fingerprint,
            ref_scan_default=self._effective_ref_scan_default(source),
        )
        progress = self._index.refresh_progress(source_key, source_fingerprint)
        failures: dict[str, RepoFailure] = {}
        successful_repos: set[str] = {
            repo for repo, status in progress.items() if status == "success"
        }
        pending_repos = [repo for repo in repos if repo.full_name not in successful_repos]
        selected: set[tuple[str, str, str]] = set()
        ignored_collections: set[str] = set()
        skipped_files: list[SkippedDependencyFile] = []
        skipped_refs: list[SkippedRef] = []
        checked_at = datetime.now(UTC)
        changed_refs = 0
        unchanged_refs = 0
        probe_kinds = self._probe_kinds(source)
        probe_mode = self._probe_mode(source)
        probed = _Probed()
        analyser = analyser_version()

        for batch_index, batch in enumerate(
            batched(pending_repos, self._repo_batch_size, strict=False)
        ):
            batch_failures = self._probe_batch(batch, probed, kinds=probe_kinds, mode=probe_mode)
            tasks = [
                self._repo_refresh_task(source, repo, probed.repos[repo.full_name])
                for repo in batch
                if repo.full_name in probed.repos
            ]
            skipped_refs.extend(skipped for task in tasks for skipped in task.skipped)
            batch_selected: set[tuple[str, str, str]] = set()
            batch_scans: list[RefScan] = []
            batch_touches: list[RefScanTouch] = []
            batch_repo_metadata: list[SourceRepoMetadata] = []
            batch_statuses: dict[str, str] = {}
            batch_analysed: list[AnalysedFile] = []
            batch_listings: list[CommitListing] = []

            for full_name, outcome in self._refresh_repos(
                tasks,
                source_key=source_key,
                resolver=resolver,
                aliases_fingerprint=aliases_fingerprint,
                paths=paths,
                checked_at=checked_at,
                analyser=analyser,
            ):
                if isinstance(outcome, RepoFailure):
                    batch_failures[full_name] = outcome
                    continue
                successful_repos.add(full_name)
                batch_statuses[full_name] = "success"
                batch_selected.update(outcome.selected)
                ignored_collections.update(outcome.ignored_collections)
                skipped_files.extend(outcome.skipped_files)
                batch_scans.extend(outcome.scans)
                batch_touches.extend(outcome.touches)
                batch_repo_metadata.append(outcome.repo_metadata)
                batch_analysed.extend(outcome.analysed)
                batch_listings.extend(outcome.listings)

            failures.update(batch_failures)

            if batch_statuses:
                selected.update(batch_selected)
                changed_refs += len(batch_scans)
                unchanged_refs += len(batch_touches)
                self._index.commit_source_ref_partial_refresh(
                    source_key,
                    scans=tuple(batch_scans),
                    touches=tuple(batch_touches),
                    keep=batch_selected,
                    repo_metadata=tuple(batch_repo_metadata),
                    processed_repos=frozenset(batch_statuses),
                    source_fingerprint=source_fingerprint,
                    progress_statuses=batch_statuses,
                    analysed=tuple(batch_analysed),
                    listings=tuple(batch_listings),
                    analyser=analyser,
                )

            repos_left = len(pending_repos) - (batch_index + 1) * self._repo_batch_size
            if repos_left > 0 and probed.spent():
                return self._refresh_result(
                    source_key=source_key,
                    repos=repos,
                    selected=selected,
                    ignored_collections=ignored_collections,
                    skipped_files=skipped_files,
                    skipped_refs=skipped_refs,
                    changed_refs=changed_refs,
                    unchanged_refs=unchanged_refs,
                    failures=failures,
                    probed=probed,
                    completed=False,
                    pause_reason=_pause_reason(probed.spent()),
                    note=note,
                )

        # When every expanded repo failed there is nothing trustworthy to mark
        # complete: leave cached data and freshness untouched so the run does
        # not look fresh. An empty source is a successful refresh and still
        # prunes now-unselected repos.
        if not repos or successful_repos:
            self._index.complete_source_ref_refresh(
                source_key,
                source_repos=frozenset(repo.full_name for repo in repos),
                scanned_at=checked_at,
            )
        else:
            self._index.clear_refresh_progress(source_key)
        return self._refresh_result(
            source_key=source_key,
            repos=repos,
            selected=selected,
            ignored_collections=ignored_collections,
            skipped_files=skipped_files,
            skipped_refs=skipped_refs,
            changed_refs=changed_refs,
            unchanged_refs=unchanged_refs,
            failures=failures,
            probed=probed,
            completed=True,
            note=note,
        )

    def _refresh_result(
        self,
        *,
        source_key: str,
        repos: list[ProbeTarget],
        selected: set[tuple[str, str, str]],
        ignored_collections: set[str],
        skipped_files: list[SkippedDependencyFile],
        skipped_refs: list[SkippedRef],
        changed_refs: int,
        unchanged_refs: int,
        failures: dict[str, RepoFailure],
        probed: _Probed,
        completed: bool,
        pause_reason: str | None = None,
        note: str | None = None,
    ) -> RefreshResult:
        status = self._index.status(source_key)
        refs = status.refs if completed and status is not None else len(selected)
        edges = status.edges if completed and status is not None else 0
        return RefreshResult(
            source_key=source_key,
            completed=completed,
            pause_reason=pause_reason,
            repos=len(repos),
            refs=refs,
            edges=edges,
            ignored_collections=tuple(sorted(ignored_collections)),
            changed_refs=changed_refs,
            unchanged_refs=unchanged_refs,
            failures=tuple(failures[repo] for repo in sorted(failures)),
            skipped_files=_dedupe_skipped_files(skipped_files),
            skipped_refs=tuple(skipped_refs),
            probe_fallbacks=probed.fallbacks,
            budgets=tuple(probed.budgets.values()),
            selection_note=note,
        )

    def _refresh_repos(
        self,
        tasks: list[_RepoRefreshTask],
        *,
        source_key: str,
        resolver: IdentityResolver,
        aliases_fingerprint: str,
        paths: list[str],
        checked_at: datetime,
        analyser: str,
    ) -> Iterable[tuple[str, _RepoRefreshResult | RepoFailure]]:
        """Run per-repo fetch/parse workers; collect results or failures."""
        paths_fingerprint = _dependency_paths_fingerprint(paths)
        total = len(tasks)
        done = 0
        changed = 0
        outcomes: list[tuple[str, _RepoRefreshResult | RepoFailure]] = []

        def outcome_of(task: _RepoRefreshTask) -> _RepoRefreshResult | RepoFailure:
            try:
                return self._refresh_repo(
                    task,
                    source_key=source_key,
                    resolver=resolver,
                    paths=paths,
                    paths_fingerprint=paths_fingerprint,
                    aliases_fingerprint=aliases_fingerprint,
                    checked_at=checked_at,
                    analyser=analyser,
                )
            except UntapedError as exc:  # the repo store's or the index's (no HTTP here)
                return RepoFailure(
                    repo=task.repo.full_name,
                    reason=str(exc) or type(exc).__name__,
                    category=exc.category,
                )

        def record(task: _RepoRefreshTask, outcome: _RepoRefreshResult | RepoFailure) -> None:
            nonlocal done, changed
            done += 1
            changed += len(outcome.scans) if isinstance(outcome, _RepoRefreshResult) else 0
            self._progress.update(
                f"{changed} changed", phase="fetching changes", done=done, total=total
            )
            outcomes.append((task.repo.full_name, outcome))

        bounded_map(outcome_of, tasks, concurrency=self._concurrency, on_each=record)
        return outcomes

    def _probe_batch(
        self,
        batch: Sequence[ProbeTarget],
        probed: _Probed,
        *,
        kinds: Sequence[str],
        mode: ProbeMode,
    ) -> dict[str, RepoFailure]:
        """Probe ``batch``'s refs into ``probed``; each repo that could not be read, by name."""
        by_url = {repo.url: repo for repo in batch}
        result = self._probe(
            list(by_url),
            kinds=kinds,
            default_branch_only=mode == "default_branch",
            default_branches={
                url: repo.default_branch
                for url, repo in by_url.items()
                if repo.default_branch != "HEAD"
            },
            on_progress=self._probe_progress,
        )
        probed.fallbacks.update(
            {by_url[url].full_name: why for url, why in result.fallbacks.items() if url in by_url}
        )
        probed.budgets.update({(budget.plugin, budget.host): budget for budget in result.budgets})
        failures: dict[str, RepoFailure] = {}
        for url, repo in by_url.items():
            row = result.repos.get(url)
            if row is None:
                failures[repo.full_name] = RepoFailure(
                    repo=repo.full_name, reason="ref probe did not read it"
                )
            elif row.failure is not None:
                failures[repo.full_name] = RepoFailure(
                    repo=repo.full_name,
                    reason=f"ref probe failed: {row.failure.message}",
                    category=row.failure.category,
                )
            else:
                probed.repos[repo.full_name] = ProbedRepo(
                    default_branch=row.default_branch, refs=_git_refs(row.refs, kinds)
                )
        return failures

    def _probe_kinds(self, source: SourceDefinition) -> tuple[str, ...]:
        """Union of ref kinds needed across all repos.

        Kind selection in :func:`source_ref_selections` depends only on
        ``ref_kinds``/``ref_patterns``/``ref_scan_default``; the default
        branch only narrows patterns, so any placeholder works here.
        """
        selections = source_ref_selections(
            source,
            default_branch="HEAD",
            ref_scan_default=self._effective_ref_scan_default(source),
        )
        return tuple(dict.fromkeys(selection.kind for selection in selections))

    def _effective_ref_scan_default(self, source: SourceDefinition) -> RefScanDefault:
        return source.ref_scan_default or self._ref_scan_default

    def _probe_mode(self, source: SourceDefinition) -> ProbeMode:
        if (
            self._effective_ref_scan_default(source) == "default_branch"
            and not source.ref_kinds
            and not source.ref_patterns
        ):
            return "default_branch"
        return "all"

    def _repo_refresh_task(
        self,
        source: SourceDefinition,
        repo: ProbeTarget,
        probed: ProbedRepo,
    ) -> _RepoRefreshTask:
        default_branch = probed.default_branch or repo.default_branch
        selections = source_ref_selections(
            source,
            default_branch=default_branch,
            ref_scan_default=self._effective_ref_scan_default(source),
        )
        selected: dict[tuple[str, str], GitRef] = {}
        skipped: dict[tuple[str, str], SkippedRef] = {}
        for selection in selections:
            for ref in probed.refs:
                if ref.kind != selection.kind:
                    continue
                if not pattern_matches(ref.name, selection.patterns):
                    continue
                # A name git allows but the store refuses (``-wip``) is left
                # out with a warning rather than failing the whole repo.
                refusal = self._git.refusal(ref)
                if refusal is not None:
                    skipped[(ref.kind, ref.name)] = SkippedRef(
                        repo=repo.full_name, kind=ref.kind, ref=ref.name, reason=refusal
                    )
                    continue
                selected[(ref.kind, ref.name)] = ref
        return _RepoRefreshTask(
            repo=repo,
            default_branch=default_branch,
            refs=tuple(selected[key] for key in sorted(selected)),
            skipped=tuple(skipped[key] for key in sorted(skipped)),
        )

    def _refresh_repo(
        self,
        task: _RepoRefreshTask,
        *,
        source_key: str,
        resolver: IdentityResolver,
        paths: list[str],
        paths_fingerprint: str,
        aliases_fingerprint: str,
        checked_at: datetime,
        analyser: str,
    ) -> _RepoRefreshResult:
        repo = task.repo
        selected: set[tuple[str, str, str]] = set()
        ignored_collections: set[str] = set()
        pending_scans: list[RefScan] = []
        pending_touches: list[RefScanTouch] = []
        clone_url = repo.url
        metadata_by_ref = self._ref_scan_metadata(source_key, repo.full_name, task.refs)
        changed_refs: list[GitRef] = []
        for ref in task.refs:
            selected.add((repo.full_name, ref.kind, ref.name))
            metadata = metadata_by_ref.get((ref.kind, ref.name))
            if (
                metadata is not None
                and metadata.source_sha == ref.sha
                and metadata.dependency_paths_fingerprint == paths_fingerprint
                and metadata.aliases_fingerprint == aliases_fingerprint
                and metadata.clone_url == clone_url
            ):
                pending_touches.append(
                    RefScanTouch(
                        source_key=source_key,
                        source_repo=repo.full_name,
                        ref_kind=ref.kind,
                        source_ref=ref.name,
                        checked_at=checked_at,
                    )
                )
                continue
            changed_refs.append(ref)
        repo_metadata = SourceRepoMetadata(
            source_key=source_key,
            source_repo=repo.full_name,
            default_branch=task.default_branch,
            url=repo.url,
        )
        # A ref whose dependencies did not change can still lack its variable
        # analysis (new in this release, or made by another analyser version).
        unanalysed = self._index.commits_needing_analysis(
            repo.full_name, {ref.sha for ref in task.refs}, analyser=analyser
        )
        fetched = [ref for ref in task.refs if ref in changed_refs or ref.sha in unanalysed]
        if not fetched:
            return _RepoRefreshResult(
                selected=frozenset(selected),
                scans=(),
                touches=tuple(pending_touches),
                repo_metadata=repo_metadata,
                ignored_collections=frozenset(),
                skipped_files=(),
            )

        self._git.fetch(clone_url, fetched)
        analysed, listings, analysis_skipped = self._analyse_variables(
            clone_url,
            repo=repo.full_name,
            refs=[ref for ref in task.refs if ref.sha in unanalysed],
            analyser=analyser,
        )
        files_by_sha = (
            self._git.read_files(clone_url, [ref.sha for ref in changed_refs], paths)
            if changed_refs
            else {}
        )
        parsed_by_sha = {
            sha: _parse_dependency_files(contents, paths) for sha, contents in files_by_sha.items()
        }
        repo_skipped_files: list[SkippedDependencyFile] = []
        for ref in changed_refs:
            parsed = parsed_by_sha[ref.sha]
            ignored_collections.update(parsed.ignored_collections)
            repo_skipped_files.extend(
                SkippedDependencyFile(
                    repo=repo.full_name,
                    ref=ref.name,
                    source_path=warning.source_path,
                    reason=warning.reason,
                )
                for warning in parsed.warnings
            )
            edges = self._edges_from_reports(
                parsed,
                repo=repo.full_name,
                ref=ref,
                resolver=resolver,
            )
            pending_scans.append(
                RefScan(
                    source_key=source_key,
                    source_repo=repo.full_name,
                    ref_kind=ref.kind,
                    source_ref=ref.name,
                    source_sha=ref.sha,
                    clone_url=clone_url,
                    dependency_paths_fingerprint=paths_fingerprint,
                    aliases_fingerprint=aliases_fingerprint,
                    checked_at=checked_at,
                    indexed_at=checked_at,
                    dependencies=tuple(edges),
                )
            )

        # A dependency file the parser already skipped says so once, not twice.
        parsed_skips = {(item.ref, item.source_path) for item in repo_skipped_files}
        analysis_skipped = [
            item for item in analysis_skipped if (item.ref, item.source_path) not in parsed_skips
        ]
        return _RepoRefreshResult(
            selected=frozenset(selected),
            scans=tuple(pending_scans),
            touches=tuple(pending_touches),
            repo_metadata=repo_metadata,
            ignored_collections=frozenset(ignored_collections),
            skipped_files=_dedupe_skipped_files([*repo_skipped_files, *analysis_skipped]),
            analysed=analysed,
            listings=listings,
        )

    def _analyse_variables(
        self, url: str, *, repo: str, refs: list[GitRef], analyser: str
    ) -> tuple[tuple[AnalysedFile, ...], tuple[CommitListing, ...], list[SkippedDependencyFile]]:
        """Analyse the selected files of each of ``refs``' commits not analysed yet.

        Files are keyed by content and path, so a file shared by several
        commits (or analysed by an earlier refresh at this analyser version)
        is read and analysed once.
        """
        from untaped_ansible.domain.variables import analyse, selected  # noqa: PLC0415  # Jinja2

        shas = list(dict.fromkeys(ref.sha for ref in refs))
        listings = tuple(
            CommitListing(
                repo=repo,
                sha=sha,
                files=tuple(
                    (path, blob) for path, blob in self._git.list_files(url, sha) if selected(path)
                ),
            )
            for sha in shas
        )
        wanted = {(blob, path) for listing in listings for path, blob in listing.files}
        unknown = sorted(wanted - self._index.analysed_files(wanted, analyser=analyser))
        contents = self._git.read_blobs(url, [blob for blob, _ in unknown])
        analysed = tuple(
            AnalysedFile(blob=blob, path=path, analysis=analyse(path, contents[blob]))
            for blob, path in unknown
        )
        reasons = {
            (item.blob, item.path): item.analysis.skipped
            for item in analysed
            if item.analysis.skipped is not None
        }
        # Each skipped file content is reported once, at the first ref holding it.
        skipped: dict[tuple[str, str], SkippedDependencyFile] = {}
        ref_at = {sha: next(ref.name for ref in refs if ref.sha == sha) for sha in shas}
        for listing in listings:
            for path, blob in listing.files:
                reason = reasons.get((blob, path))
                if reason is not None and (blob, path) not in skipped:
                    skipped[(blob, path)] = SkippedDependencyFile(
                        repo=repo,
                        ref=ref_at[listing.sha],
                        source_path=path,
                        reason=reason,
                        analysis=True,
                    )
        return analysed, listings, list(skipped.values())

    def _expand_repos(self, source: SourceDefinition) -> tuple[list[ProbeTarget], str | None]:
        """The repos the source's plugin selects, and its note when it listed them instead.

        A failure propagates: a selection the plugin can't answer is a
        source problem, not a per-repo refresh failure.
        """
        selection = self._select(source)
        targets: dict[str, ProbeTarget] = {}
        for repo in selection.repos:  # a repo the plugin names twice is scanned once
            targets.setdefault(repo.name, _target(repo))
        self._progress.update(phase="selecting repos", done=len(targets), total=len(targets))
        return list(targets.values()), selection.note

    def _probe_progress(self, done: int, total: int) -> None:
        self._progress.update(phase="probing refs", done=done, total=total)

    def _ref_scan_metadata(
        self,
        source_key: str,
        repo: str,
        refs: tuple[GitRef, ...],
    ) -> dict[tuple[str, str], RefScanMetadata]:
        return self._index.ref_scans(
            source_key,
            repo,
            ((ref.kind, ref.name) for ref in refs),
        )

    def _edges_from_reports(
        self,
        parsed: _ParsedDependencyFiles,
        *,
        repo: str,
        ref: GitRef,
        resolver: IdentityResolver,
    ) -> list[IndexedDependency]:
        edges: list[IndexedDependency] = []
        for path, report in parsed.reports:
            for declaration in report.dependencies:
                resolved = resolver.resolve(declaration)
                edges.append(
                    IndexedDependency(
                        source_repo=repo,
                        source_ref=ref.name,
                        source_ref_kind=ref.kind,
                        source_sha=ref.sha,
                        dependency_repo=resolved.repo,
                        dependency_name=declaration.name,
                        dependency_version=declaration.version,
                        source_path=path,
                        unresolved=resolved.unresolved,
                    )
                )
        return edges


def _parse_dependency_files(contents: dict[str, str], paths: list[str]) -> _ParsedDependencyFiles:
    reports: list[tuple[str, ParseReport]] = []
    ignored_collections: set[str] = set()
    warnings: list[ParseWarning] = []
    for path in paths:
        content = contents.get(path)
        if content is None:
            continue
        report = parse_dependency_file(path, content)
        ignored_collections.update(report.ignored_collections)
        warnings.extend(report.warnings)
        reports.append((path, report))
    return _ParsedDependencyFiles(
        reports=tuple(reports),
        ignored_collections=frozenset(ignored_collections),
        warnings=tuple(warnings),
    )


def _dedupe_skipped_files(
    skipped_files: Iterable[SkippedDependencyFile],
) -> tuple[SkippedDependencyFile, ...]:
    by_key = {_skipped_file_key(skipped): skipped for skipped in skipped_files}
    return tuple(by_key[key] for key in sorted(by_key))


def _skipped_file_key(skipped: SkippedDependencyFile) -> tuple[str, str, str, str]:
    return (
        skipped.repo,
        skipped.ref or "",
        skipped.source_path,
        skipped.reason,
    )


def _target(repo: Repo) -> ProbeTarget:
    """A selected repo as a source repository: its name and URL as its plugin gives them."""
    return ProbeTarget(
        full_name=repo.name, url=repo.url, default_branch=repo.default_branch or "HEAD"
    )


def _dependency_paths_fingerprint(paths: list[str]) -> str:
    """Fingerprint of which dependency files are read and how they are parsed."""
    inputs = {"paths": paths, "parser_version": parser_version()}
    payload = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _aliases_fingerprint(aliases: dict[str, str], hosts: Iterable[str]) -> str:
    """Fingerprint of everything identity resolution depends on: aliases and the URL hosts."""
    resolution = {"aliases": aliases, "hosts": sorted(hosts)}
    payload = json.dumps(resolution, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def source_refresh_fingerprint(
    source: SourceDefinition,
    *,
    repos: list[ProbeTarget],
    paths_fingerprint: str,
    aliases_fingerprint: str,
    ref_scan_default: RefScanDefault,
) -> str:
    payload = {
        "source": source.model_dump(mode="json", exclude={"name"}),
        "repos": sorted({repo.full_name for repo in repos}),
        "paths_fingerprint": paths_fingerprint,
        "aliases_fingerprint": aliases_fingerprint,
        "ref_scan_default": ref_scan_default,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _git_refs(refs: Mapping[str, str], kinds: Sequence[str]) -> tuple[GitRef, ...]:
    """The probed ``refs/<kind>/<name> → commit`` of ``kinds``, as sorted :class:`GitRef`."""
    found = [
        GitRef(kind=kind, name=ref.removeprefix(f"refs/{kind}/"), sha=sha)
        for ref, sha in refs.items()
        for kind in kinds
        if ref.startswith(f"refs/{kind}/")
    ]
    return tuple(sorted(found, key=lambda ref: (ref.kind, ref.name)))


def _pause_reason(spent: Sequence[HostBudget]) -> str:
    hosts = ", ".join(
        f"{budget.host} ({budget.remaining} left)" if budget.remaining is not None else budget.host
        for budget in spent
    )
    return f"the ref probe budget is spent on {hosts}"
