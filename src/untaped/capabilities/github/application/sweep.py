"""Application use cases for repository sweep queries and corpus warm-up syncs."""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from untaped.capabilities.github.application.inventory import (
    RepositoryInventoryItem,
    RepositoryInventoryScope,
)
from untaped.capabilities.github.application.ports import GitCorpus
from untaped.capabilities.github.domain import (
    CODEOWNERS_LOCATIONS,
    CorpusFailure,
    CorpusFreshness,
    CorpusRepoTarget,
    CorpusSyncOutcome,
    GrepHit,
    GrepSpec,
    RefEvaluation,
    RefSelector,
    RepoSweepOutcome,
    SweepQuery,
    covers,
    parse_codeowners,
    ref_display_names,
    ref_matches,
    unchanged_upstream,
)
from untaped.capabilities.github.domain.errors import GitCorpusError, is_global_github_failure
from untaped.capability_api import (
    ConfigError,
    ProgressHandle,
    UntapedError,
    UsageError,
    bounded_map,
    plural,
)

InventoryResolver = Callable[[RepositoryInventoryScope], tuple[RepositoryInventoryItem, ...]]
AuthHeaderSupplier = Callable[[], str | None]
# Parallel per-repo API lookups; kept low to stay clear of GitHub's secondary rate limits.
_API_CONCURRENCY = 4


@dataclass(frozen=True)
class SweepOptions:
    """Options for a sweep run."""

    scope: RepositoryInventoryScope
    stdin_repos: tuple[str, ...]
    include_archived: bool
    query: SweepQuery
    sync: Literal["auto", "force", "off"]
    max_age_seconds: int
    depth: int
    parallel: int
    owners: bool
    # Piped records complete enough to skip the per-repo API lookup.
    stdin_items: tuple[RepositoryInventoryItem, ...] = ()

    def __post_init__(self) -> None:
        if self.depth < 0:
            raise ValueError("depth must be non-negative")
        if self.parallel < 1:
            raise ValueError("parallel must be positive")
        if self.max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")


@dataclass(frozen=True)
class CorpusSyncOptions:
    """Options for warming the corpus without a query (``cache sync``)."""

    scope: RepositoryInventoryScope
    stdin_repos: tuple[str, ...]
    include_archived: bool
    refs: RefSelector
    refresh: bool
    max_age_seconds: int
    depth: int
    parallel: int
    stdin_items: tuple[RepositoryInventoryItem, ...] = ()


@dataclass(frozen=True)
class SweepMatch:
    """One deduped content match, possibly reachable from multiple refs."""

    full_name: str
    refs: tuple[str, ...]
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class SweepReport:
    """Result of a repository sweep."""

    rows: tuple[RepoSweepOutcome, ...]
    matches: tuple[SweepMatch, ...]
    unscanned: tuple[CorpusFailure, ...]
    scanned: int
    refreshed: int
    cached: int
    oldest_fetched_at: datetime | None
    # Repos whose refresh failed but whose covering cached copy was scanned.
    stale: tuple[CorpusFailure, ...] = ()


@dataclass(frozen=True)
class _ReadyRepo:
    repo: CorpusRepoTarget
    fetched_at: datetime | None
    refreshed: bool
    refresh_error: str | None = None
    # True when GitHub reported no push since the cached fetch, so none ran.
    unchanged: bool = False


@dataclass(frozen=True)
class _RepoScan:
    outcome: RepoSweepOutcome | None
    matches: tuple[_ContentMatch, ...]


@dataclass
class _TreeScan:
    hits: dict[str, int]
    owner_paths: set[str]
    grep_hits: list[GrepHit]


@dataclass(frozen=True)
class _ContentMatch:
    full_name: str
    ref: str
    path: str
    line: int
    text: str


class _NoProgress:
    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        return None

    def log(self, line: str) -> None:
        return None


class _TreeScanner:
    """Evaluate a query on each unique tree, dropping trees that can no longer match.

    A dropped tree keeps the hits gathered so far; ``ref_matches`` still
    rejects it because its failed predicate stays recorded (a missing label
    reads as zero). Cheap file predicates run first, then one ``git grep`` per
    pattern across all live trees; a negative pattern only needs
    ``git grep -q``, which stops at the first hit.
    """

    def __init__(
        self,
        corpus: GitCorpus,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        trees: tuple[str, ...],
        query: SweepQuery,
    ) -> None:
        self._corpus = corpus
        self._repo = repo
        self._root = root
        self._query = query
        self._scans = {tree: _TreeScan(hits={}, owner_paths=set(), grep_hits=[]) for tree in trees}
        self._live = list(trees)
        self._paths: dict[str, tuple[str, ...]] = {}

    def run(self) -> dict[str, _TreeScan]:
        query = self._query
        for glob in query.has_files:
            for tree in self._live:
                matched = _matching_paths(self._tree_paths(tree), glob)
                self._scans[tree].hits[f"has-file:{glob}"] = 1 if matched else 0
                self._scans[tree].owner_paths.update(matched)
            self._keep(f"has-file:{glob}", positive=True)
        for glob in query.lacks_files:
            for tree in self._live:
                found = _matching_paths(self._tree_paths(tree), glob)
                self._scans[tree].hits[f"lacks-file:{glob}"] = 1 if found else 0
            self._keep(f"lacks-file:{glob}", positive=False)
        for pattern in query.greps:
            self._grep(pattern)
        if query.any_mode and (query.greps or query.has_files):
            labels = _positive_labels(query)
            self._live = [
                tree
                for tree in self._live
                if any(self._scans[tree].hits.get(label, 0) for label in labels)
            ]
        for pattern in query.not_greps:
            spec = _spec(query, pattern)
            for tree in self._live:
                hit = self._corpus.tree_has_match(self._repo, root=self._root, tree=tree, spec=spec)
                self._scans[tree].hits[f"not-grep:{pattern}"] = 1 if hit else 0
            self._keep(f"not-grep:{pattern}", positive=False)
        return self._scans

    def _grep(self, pattern: str) -> None:
        label = f"grep:{pattern}"
        found = (
            self._corpus.grep_trees(
                self._repo,
                root=self._root,
                trees=tuple(self._live),
                spec=_spec(self._query, pattern),
            )
            if self._live
            else {}
        )
        for tree in self._live:
            hits = found.get(tree, ())
            scan = self._scans[tree]
            scan.hits[label] = len(hits)
            scan.owner_paths.update(hit.path for hit in hits)
            scan.grep_hits.extend(hits)
        self._keep(label, positive=True)

    def _tree_paths(self, tree: str) -> tuple[str, ...]:
        if tree not in self._paths:
            self._paths[tree] = self._corpus.tree_paths(self._repo, root=self._root, ref=tree)
        return self._paths[tree]

    def _keep(self, label: str, *, positive: bool) -> None:
        # Negative predicates are always ANDed; positive ones only without --any.
        if positive and self._query.any_mode:
            return
        self._live = [
            tree for tree in self._live if (self._scans[tree].hits[label] > 0) == positive
        ]


class Sweep:
    """Run a sweep query across repository inventory and local corpus refs."""

    def __init__(
        self,
        *,
        inventory: InventoryResolver,
        corpus: GitCorpus,
        root: Path,
        auth_header: AuthHeaderSupplier,
    ) -> None:
        self._inventory = inventory
        self._corpus = corpus
        self._root = root
        self._auth_header = auth_header

    def __call__(
        self, options: SweepOptions, *, progress: ProgressHandle | None = None
    ) -> SweepReport:
        options.query.validate()
        progress = progress or _NoProgress()
        progress.update("Resolving repositories…", new_phase=True)
        repos, scope_failures = self._resolve_scope(options)

        ready: list[_ReadyRepo] = []
        scans: list[_RepoScan] = []
        failures: list[CorpusFailure] = []
        counts = {"fetched": 0, "failed": 0}

        # Sync and scan run in one worker per repo, so scanning starts as soon
        # as a repo's fetch ends instead of after the slowest fetch.
        def sweep_one(
            repo: CorpusRepoTarget,
        ) -> tuple[_ReadyRepo | None, _RepoScan | CorpusFailure]:
            prepared = self._prepare(repo, options)
            if isinstance(prepared, CorpusFailure):
                return None, prepared
            try:
                return prepared, self._scan_repo(prepared, options)
            except (GitCorpusError, OSError) as exc:
                return prepared, _failure(repo, exc)

        def record(
            _repo: CorpusRepoTarget,
            outcome: tuple[_ReadyRepo | None, _RepoScan | CorpusFailure],
        ) -> None:
            prepared, result = outcome
            if prepared is not None:
                ready.append(prepared)
                counts["fetched"] += prepared.refreshed
            if isinstance(result, CorpusFailure):
                failures.append(result)
                counts["failed"] += 1
            else:
                scans.append(result)
            done = len(scans) + len(failures)
            progress.update(
                f"Sweeping {done}/{plural(len(repos), 'repo')} "
                f"({counts['fetched']} fetched, {counts['failed']} failed)",
                fraction=done / len(repos),
            )

        if repos:
            progress.update(f"Sweeping {plural(len(repos), 'repo')}…", new_phase=True)
        bounded_map(sweep_one, repos, concurrency=options.parallel, on_each=record)
        rows = tuple(
            sorted(
                (scan.outcome for scan in scans if scan.outcome is not None),
                key=lambda row: row.full_name,
            )
        )
        all_matches = [match for scan in scans for match in scan.matches]
        scanned_dates = [ready_repo.fetched_at for ready_repo in ready if ready_repo.fetched_at]
        refreshed = sum(1 for ready_repo in ready if ready_repo.refreshed)
        cached = len(ready) - refreshed
        return SweepReport(
            rows=rows,
            matches=_dedupe_matches(all_matches),
            unscanned=(*scope_failures, *sorted(failures, key=lambda failure: failure.repo)),
            scanned=len(scans),
            refreshed=refreshed,
            cached=cached,
            oldest_fetched_at=min(scanned_dates) if scanned_dates else None,
            stale=tuple(
                sorted(
                    (
                        CorpusFailure(
                            repo=ready_repo.repo.full_name, reason=ready_repo.refresh_error
                        )
                        for ready_repo in ready
                        if ready_repo.refresh_error is not None
                    ),
                    key=lambda failure: failure.repo,
                )
            ),
        )

    def _resolve_scope(
        self, options: SweepOptions
    ) -> tuple[tuple[CorpusRepoTarget, ...], tuple[CorpusFailure, ...]]:
        if options.sync == "off":
            return self._resolve_offline_scope(options), ()

        return _resolve_online_scope(
            self._inventory,
            scope=options.scope,
            stdin_repos=options.stdin_repos,
            stdin_items=options.stdin_items,
            include_archived=options.include_archived,
            parallel=options.parallel,
        )

    def _resolve_offline_scope(self, options: SweepOptions) -> tuple[CorpusRepoTarget, ...]:
        if options.scope.teams:
            raise UsageError("--team requires the API and cannot be combined with --cached")
        # GitHub owner and repo names are case-insensitive.
        names = {
            name.casefold()
            for name in (
                *options.scope.repos,
                *options.stdin_repos,
                *(item.full_name for item in options.stdin_items),
            )
        }
        owners = {org.casefold() for org in options.scope.orgs}
        rows = self._corpus.list_repos(root=self._root)
        targets: list[CorpusRepoTarget] = []
        for row in rows:
            if not options.include_archived and row.archived:
                continue
            if owners and row.repo.partition("/")[0].casefold() not in owners:
                continue
            if names and row.repo.casefold() not in names:
                continue
            targets.append(
                CorpusRepoTarget(
                    full_name=row.repo,
                    default_branch=row.ref,
                    clone_url=row.clone_url,
                    archived=row.archived,
                )
            )
        if not targets:
            raise ConfigError("corpus has no repos in scope; run without --cached to populate")
        return tuple(sorted(targets, key=lambda repo: repo.full_name))

    def _prepare(self, repo: CorpusRepoTarget, options: SweepOptions) -> _ReadyRepo | CorpusFailure:
        """Make one repo's cached copy current enough to scan (sync mode permitting)."""
        return _prepare_repo(
            self._corpus,
            repo,
            root=self._root,
            selector=options.query.refs,
            sync=options.sync,
            max_age_seconds=options.max_age_seconds,
            depth=options.depth,
            auth_header=self._auth_header,
        )

    def _scan_repo(self, ready: _ReadyRepo, options: SweepOptions) -> _RepoScan:
        refs_matched: list[str] = []
        aggregate_hits: dict[str, int] = {}
        owner_paths: set[str] = set()
        matches: list[_ContentMatch] = []
        # Scan by full refname (a branch and a tag may share a short name);
        # report the unambiguous short display name. Refs that share a tree
        # (a tag on a branch tip, say) are scanned once.
        refs = self._corpus.local_refs(ready.repo, root=self._root, selector=options.query.refs)
        display_names = ref_display_names(ref.name for ref in refs)
        tree_scans = self._scan_trees(
            ready.repo, tuple(dict.fromkeys(ref.tree for ref in refs)), options.query
        )
        for ref in refs:
            tree_scan = tree_scans[ref.tree]
            display = display_names[ref.name]
            evaluation = RefEvaluation(ref=display, hits=tree_scan.hits)
            if not ref_matches(options.query, evaluation):
                continue
            refs_matched.append(display)
            owner_paths.update(tree_scan.owner_paths)
            matches.extend(
                _content_match(ready.repo.full_name, display, hit) for hit in tree_scan.grep_hits
            )
            for label, count in tree_scan.hits.items():
                aggregate_hits[label] = max(aggregate_hits.get(label, 0), count)

        if not refs_matched:
            return _RepoScan(outcome=None, matches=())

        owners = self._owners_for(ready.repo, paths=owner_paths) if options.owners else ()
        return _RepoScan(
            outcome=RepoSweepOutcome(
                full_name=ready.repo.full_name,
                clone_url=ready.repo.clone_url,
                matched=True,
                refs_matched=tuple(refs_matched),
                hits=aggregate_hits,
                owners=owners,
                synced_at=ready.fetched_at.isoformat() if ready.fetched_at else None,
            ),
            matches=tuple(matches),
        )

    def _scan_trees(
        self, repo: CorpusRepoTarget, trees: tuple[str, ...], query: SweepQuery
    ) -> dict[str, _TreeScan]:
        return _TreeScanner(self._corpus, repo, root=self._root, trees=trees, query=query).run()

    def _owners_for(self, repo: CorpusRepoTarget, *, paths: Iterable[str]) -> tuple[str, ...]:
        branch = repo.default_branch
        if not branch:
            return ()
        try:
            text = self._corpus.read_first_blob(
                repo, root=self._root, ref=f"refs/heads/{branch}", paths=CODEOWNERS_LOCATIONS
            )
        except GitCorpusError:
            return ()
        if text is None:
            return ()
        rules = parse_codeowners(text)
        owner_rows: list[str] = []
        sorted_paths = tuple(sorted(paths))
        if not sorted_paths:
            owner_rows.extend(rules.default_owners())
        else:
            for path in sorted_paths:
                owner_rows.extend(rules.owners_for(path))
        return tuple(dict.fromkeys(owner_rows))


def _resolve_online_scope(
    inventory: InventoryResolver,
    *,
    scope: RepositoryInventoryScope,
    stdin_repos: tuple[str, ...],
    stdin_items: tuple[RepositoryInventoryItem, ...],
    include_archived: bool,
    parallel: int,
) -> tuple[tuple[CorpusRepoTarget, ...], tuple[CorpusFailure, ...]]:
    """Expand scopes through the API into sorted corpus targets plus per-name failures."""
    items: dict[str, RepositoryInventoryItem] = {}
    if scope.orgs or scope.teams:
        listed = RepositoryInventoryScope(orgs=scope.orgs, teams=scope.teams)
        for item in inventory(listed):
            items.setdefault(item.full_name, item)
    # Piped repo records already carry what a sweep needs: no API call.
    items.update((item.full_name, item) for item in stdin_items)
    # Expand explicit names one at a time so one missing or inaccessible
    # repo becomes an unscanned row instead of aborting the whole sweep.
    # Auth and rate-limit failures hit every repo alike, so they abort.
    failures: list[CorpusFailure] = []
    names = tuple(dict.fromkeys((*scope.repos, *stdin_repos)))

    def resolve_one(name: str) -> tuple[RepositoryInventoryItem, ...] | CorpusFailure:
        try:
            return inventory(RepositoryInventoryScope(repos=(name,)))
        except UntapedError as exc:
            if is_global_github_failure(exc):
                raise
            return CorpusFailure(repo=name, reason=str(exc) or type(exc).__name__)

    def record(_name: str, resolved: tuple[RepositoryInventoryItem, ...] | CorpusFailure) -> None:
        if isinstance(resolved, CorpusFailure):
            failures.append(resolved)
        else:
            items.update((item.full_name, item) for item in resolved)

    bounded_map(resolve_one, names, concurrency=min(parallel, _API_CONCURRENCY), on_each=record)
    failures.sort(key=lambda failure: failure.repo)
    if names and len(failures) == len(names) and not items:
        detail = "; ".join(failure.reason for failure in failures)
        raise UntapedError(f"no requested repository could be resolved: {detail}")
    rows = (items[name] for name in sorted(items) if include_archived or not items[name].archived)
    return tuple(_target(item) for item in rows), tuple(failures)


def _target(item: RepositoryInventoryItem) -> CorpusRepoTarget:
    return CorpusRepoTarget(
        full_name=item.full_name,
        default_branch=item.default_branch,
        clone_url=item.clone_url,
        html_url=item.html_url,
        archived=item.archived,
        pushed_at=item.pushed_at,
    )


def _failure(repo: CorpusRepoTarget, exc: Exception) -> CorpusFailure:
    return CorpusFailure(repo=repo.full_name, reason=str(exc) or type(exc).__name__)


class SyncCorpus:
    """Fetch every repository in scope into the corpus so later sweeps start warm."""

    def __init__(
        self,
        *,
        inventory: InventoryResolver,
        corpus: GitCorpus,
        root: Path,
        auth_header: AuthHeaderSupplier,
    ) -> None:
        self._inventory = inventory
        self._corpus = corpus
        self._root = root
        self._auth_header = auth_header

    def __call__(
        self, options: CorpusSyncOptions, *, progress: ProgressHandle | None = None
    ) -> tuple[CorpusSyncOutcome, ...]:
        progress = progress or _NoProgress()
        progress.update("Resolving repositories…", new_phase=True)
        repos, failures = _resolve_online_scope(
            self._inventory,
            scope=options.scope,
            stdin_repos=options.stdin_repos,
            stdin_items=options.stdin_items,
            include_archived=options.include_archived,
            parallel=options.parallel,
        )
        outcomes = [
            CorpusSyncOutcome(repo=failure.repo, action="failed", error=failure.reason)
            for failure in failures
        ]

        def sync_one(repo: CorpusRepoTarget) -> _ReadyRepo | CorpusFailure:
            return _prepare_repo(
                self._corpus,
                repo,
                root=self._root,
                selector=options.refs,
                sync="force" if options.refresh else "auto",
                max_age_seconds=options.max_age_seconds,
                depth=options.depth,
                auth_header=self._auth_header,
            )

        def record(repo: CorpusRepoTarget, result: _ReadyRepo | CorpusFailure) -> None:
            outcomes.append(_sync_outcome(repo, result))
            done = len(outcomes) - len(failures)
            fetched = sum(outcome.action == "synced" for outcome in outcomes)
            failed = sum(outcome.failed for outcome in outcomes)
            progress.update(
                f"Syncing {done}/{plural(len(repos), 'repo')} ({fetched} fetched, {failed} failed)",
                fraction=done / len(repos),
            )

        if repos:
            progress.update(f"Syncing {plural(len(repos), 'repo')}…", new_phase=True)
        bounded_map(sync_one, repos, concurrency=options.parallel, on_each=record)
        return tuple(sorted(outcomes, key=lambda outcome: outcome.repo))


def _sync_outcome(repo: CorpusRepoTarget, result: _ReadyRepo | CorpusFailure) -> CorpusSyncOutcome:
    if isinstance(result, CorpusFailure):
        return CorpusSyncOutcome(repo=repo.full_name, action="failed", error=result.reason)
    if result.refresh_error is not None:
        return CorpusSyncOutcome(
            repo=repo.full_name,
            action="failed",
            fetched_at=result.fetched_at,
            error=result.refresh_error,
        )
    action = "synced" if result.refreshed else "unchanged" if result.unchanged else "skipped"
    return CorpusSyncOutcome(repo=repo.full_name, action=action, fetched_at=result.fetched_at)


def _prepare_repo(
    corpus: GitCorpus,
    repo: CorpusRepoTarget,
    *,
    root: Path,
    selector: RefSelector,
    sync: Literal["auto", "force", "off"],
    max_age_seconds: int,
    depth: int,
    auth_header: AuthHeaderSupplier,
) -> _ReadyRepo | CorpusFailure:
    """Bring one repo's cached copy up to date for ``selector``, fetching only when needed.

    ``auto`` fetches a copy that is missing, under-profiled, or older than
    ``max_age_seconds``; an old copy whose GitHub ``pushed_at`` has not moved
    is marked current without any Git call. ``force`` always fetches and
    ``off`` never does. A failed fetch falls back to a covering cached copy.
    """
    try:
        freshness = corpus.repo_freshness(repo, root=root)
    except (GitCorpusError, OSError) as exc:
        return _failure(repo, exc)
    if sync == "off":
        return _ReadyRepo(repo=repo, fetched_at=_freshness_datetime(freshness), refreshed=False)
    if sync == "auto" and freshness is not None and covers(freshness, selector):
        if not _expired(freshness, max_age_seconds=max_age_seconds):
            return _ReadyRepo(repo=repo, fetched_at=freshness.fetched_at, refreshed=False)
        if unchanged_upstream(freshness, repo):
            try:
                touched = corpus.touch_repo(repo, root=root)
            except GitCorpusError, OSError:
                touched = freshness.fetched_at
            return _ReadyRepo(repo=repo, fetched_at=touched, refreshed=False, unchanged=True)
    try:
        result = corpus.sync_repo(
            repo, root=root, selector=selector, depth=depth, auth_header=auth_header()
        )
    except (GitCorpusError, OSError) as exc:
        if freshness is not None and covers(freshness, selector):
            return _ReadyRepo(
                repo=repo,
                fetched_at=freshness.fetched_at,
                refreshed=False,
                refresh_error=_failure(repo, exc).reason,
            )
        return _failure(repo, exc)
    return _ReadyRepo(repo=repo, fetched_at=_parse_datetime(result.fetched_at), refreshed=True)


def _expired(freshness: CorpusFreshness, *, max_age_seconds: int) -> bool:
    return (datetime.now(UTC) - freshness.fetched_at).total_seconds() > max_age_seconds


def _spec(query: SweepQuery, pattern: str) -> GrepSpec:
    return GrepSpec(
        pattern=pattern,
        paths=query.paths,
        ignore_case=query.ignore_case,
        fixed_strings=query.fixed_strings,
        word_regexp=query.word_regexp,
    )


def _positive_labels(query: SweepQuery) -> tuple[str, ...]:
    return (
        *(f"grep:{pattern}" for pattern in query.greps),
        *(f"has-file:{glob}" for glob in query.has_files),
    )


def _freshness_datetime(freshness: CorpusFreshness | None) -> datetime | None:
    return freshness.fetched_at if freshness is not None else None


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _matching_paths(paths: tuple[str, ...], glob: str) -> tuple[str, ...]:
    return tuple(path for path in paths if fnmatch.fnmatchcase(path, glob))


def _content_match(full_name: str, ref: str, hit: GrepHit) -> _ContentMatch:
    return _ContentMatch(
        full_name=full_name,
        ref=ref,
        path=hit.path,
        line=hit.line,
        text=hit.text,
    )


def _dedupe_matches(matches: Iterable[_ContentMatch]) -> tuple[SweepMatch, ...]:
    # Insertion-ordered dicts give O(1) ref dedupe while keeping first-seen order.
    # Refs showing the same line at the same place collapse into one row.
    grouped: dict[tuple[str, str, int, str], dict[str, None]] = {}
    for match in matches:
        key = (match.full_name, match.path, match.line, match.text)
        grouped.setdefault(key, {})[match.ref] = None
    rows = [
        SweepMatch(
            full_name=full_name,
            refs=tuple(refs),
            path=path,
            line=line,
            text=text,
        )
        for (full_name, path, line, text), refs in grouped.items()
    ]
    return tuple(sorted(rows, key=lambda row: (row.full_name, row.path, row.line, row.text)))
