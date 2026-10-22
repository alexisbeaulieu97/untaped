"""Application use cases for repository sweep queries and corpus warm-up syncs."""

from __future__ import annotations

import contextlib
import fnmatch
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from untaped.sdk import (
    NO_PROGRESS,
    ConfigError,
    Failure,
    Filled,
    NeverRefreshed,
    ProgressHandle,
    UntapedError,
    UsageError,
    attribution,
    bounded_map,
    most_severe,
)
from untaped_github.application.inventory import RepositoryInventoryScope
from untaped_github.application.ports import GitCorpus
from untaped_github.domain import (
    CODEOWNERS_LOCATIONS,
    ArchivedMode,
    CorpusFailure,
    CorpusFreshness,
    CorpusRepoTarget,
    GrepHit,
    GrepSpec,
    RefEvaluation,
    RefSelector,
    SweepQuery,
    archived_allows,
    covers,
    parse_codeowners,
    ref_display_names,
    ref_matches,
    unchanged_upstream,
)
from untaped_github.domain.errors import is_global_github_failure
from untaped_github.errors import GitCorpusError
from untaped_github.records import CorpusSyncOutcome, GithubRepo, RepoSweepOutcome, SweepMatch
from untaped_github.settings import SWEEP

InventoryResolver = Callable[[RepositoryInventoryScope], tuple[GithubRepo, ...]]
# Parallel per-repo API lookups; kept low to stay clear of GitHub's secondary rate limits.
_API_CONCURRENCY = 4


@dataclass(frozen=True)
class SweepOptions:
    """Options for a sweep run."""

    scope: RepositoryInventoryScope
    stdin_repos: tuple[str, ...]
    archived: ArchivedMode
    query: SweepQuery
    # SWEEP's directive: None refreshes copies past their max age, True all, False none.
    refresh: bool | None
    parallel: int
    owners: bool
    # Piped records complete enough to skip the per-repo API lookup.
    stdin_items: tuple[GithubRepo, ...] = ()


@dataclass(frozen=True)
class CorpusSyncOptions:
    """Options for warming the corpus without a query (``cache sync``)."""

    scope: RepositoryInventoryScope
    stdin_repos: tuple[str, ...]
    archived: ArchivedMode
    refs: RefSelector
    refresh: bool
    parallel: int
    stdin_items: tuple[GithubRepo, ...] = ()


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


@dataclass(frozen=True)
class _ReadyRepo:
    repo: CorpusRepoTarget
    fetched_at: datetime | None
    refreshed: bool
    refresh_error: CorpusFailure | None = None
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
    repo: str
    ref: str
    path: str
    line: int
    text: str


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
        trees: tuple[str, ...],
        query: SweepQuery,
    ) -> None:
        self._corpus = corpus
        self._repo = repo
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
                hit = self._corpus.tree_has_match(self._repo, tree=tree, spec=spec)
                self._scans[tree].hits[f"not-grep:{pattern}"] = 1 if hit else 0
            self._keep(f"not-grep:{pattern}", positive=False)
        return self._scans

    def _grep(self, pattern: str) -> None:
        label = f"grep:{pattern}"
        found = (
            self._corpus.grep_trees(
                self._repo,
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
            self._paths[tree] = self._corpus.tree_paths(self._repo, ref=tree)
        return self._paths[tree]

    def _keep(self, label: str, *, positive: bool) -> None:
        # Negative predicates are always ANDed; positive ones only without --any.
        if positive and self._query.any_mode:
            return
        self._live = [
            tree for tree in self._live if (self._scans[tree].hits[label] > 0) == positive
        ]


class _CorpusUseCase:
    """Shared wiring for use cases that resolve inventory into the local corpus."""

    def __init__(
        self,
        *,
        inventory: InventoryResolver,
        corpus: GitCorpus,
    ) -> None:
        self._inventory = inventory
        self._corpus = corpus


class Sweep(_CorpusUseCase):
    """Run a sweep query across repository inventory and local corpus refs."""

    def __call__(
        self, options: SweepOptions, *, progress: ProgressHandle = NO_PROGRESS
    ) -> SweepReport:
        options.query.validate()
        progress.update(phase="resolving repositories")
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
            prepared = _prepare_repo(
                self._corpus,
                repo,
                selector=options.query.refs,
                refresh=options.refresh,
                progress=progress,
            )
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
                f"{counts['fetched']} fetched, {counts['failed']} failed",
                phase="sweeping",
                done=done,
                total=len(repos),
            )

        if repos:
            progress.update(phase="sweeping", done=0, total=len(repos))
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
        )

    def _resolve_scope(
        self, options: SweepOptions
    ) -> tuple[tuple[CorpusRepoTarget, ...], tuple[CorpusFailure, ...]]:
        if options.refresh is False:
            return self._resolve_offline_scope(options), ()

        return _resolve_online_scope(
            self._inventory,
            scope=options.scope,
            stdin_repos=options.stdin_repos,
            stdin_items=options.stdin_items,
            archived=options.archived,
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
        rows = self._corpus.list_repos()
        targets: list[CorpusRepoTarget] = []
        for row in rows:
            if not archived_allows(options.archived, row.archived):
                continue
            if owners and row.full_name.partition("/")[0].casefold() not in owners:
                continue
            if names and row.full_name.casefold() not in names:
                continue
            targets.append(
                CorpusRepoTarget(
                    full_name=row.full_name,
                    default_branch=row.ref,
                    clone_url=row.clone_url,
                    archived=row.archived,
                )
            )
        if not targets:
            raise ConfigError(
                "corpus has no repos in scope; run without --cached to populate",
                category="not_found",
            )
        return tuple(sorted(targets, key=lambda repo: repo.full_name))

    def _scan_repo(self, ready: _ReadyRepo, options: SweepOptions) -> _RepoScan:
        refs_matched: list[str] = []
        aggregate_hits: dict[str, int] = {}
        owner_paths: set[str] = set()
        matches: list[_ContentMatch] = []
        # Scan by full refname (a branch and a tag may share a short name);
        # report the unambiguous short display name. Refs that share a tree
        # (a tag on a branch tip, say) are scanned once.
        refs = self._corpus.local_refs(ready.repo, selector=options.query.refs)
        display_names = ref_display_names(ref.name for ref in refs)
        tree_scans = _TreeScanner(
            self._corpus,
            ready.repo,
            trees=tuple(dict.fromkeys(ref.tree for ref in refs)),
            query=options.query,
        ).run()
        for ref in refs:
            tree_scan = tree_scans[ref.tree]
            display = display_names[ref.name]
            evaluation = RefEvaluation(ref=display, hits=tree_scan.hits)
            if not ref_matches(options.query, evaluation):
                continue
            refs_matched.append(display)
            owner_paths.update(tree_scan.owner_paths)
            matches.extend(
                _ContentMatch(ready.repo.full_name, display, hit.path, hit.line, hit.text)
                for hit in tree_scan.grep_hits
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
                html_url=ready.repo.html_url,
                default_branch=ready.repo.default_branch,
                archived=ready.repo.archived,
                pushed_at=ready.repo.pushed_at,
                matched=True,
                refs_matched=tuple(refs_matched),
                hits=aggregate_hits,
                owners=owners,
                fetched_at=ready.fetched_at.isoformat() if ready.fetched_at else None,
            ),
            matches=tuple(matches),
        )

    def _owners_for(self, repo: CorpusRepoTarget, *, paths: Iterable[str]) -> tuple[str, ...]:
        branch = repo.default_branch
        if not branch:
            return ()
        try:
            text = self._corpus.read_first_blob(
                repo, ref=f"refs/heads/{branch}", paths=CODEOWNERS_LOCATIONS
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
    stdin_items: tuple[GithubRepo, ...],
    archived: ArchivedMode,
    parallel: int,
) -> tuple[tuple[CorpusRepoTarget, ...], tuple[CorpusFailure, ...]]:
    """Expand scopes through the API into sorted corpus targets plus per-name failures."""
    items: dict[str, GithubRepo] = {}
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

    def resolve_one(name: str) -> tuple[GithubRepo, ...] | CorpusFailure:
        try:
            return inventory(RepositoryInventoryScope(repos=(name,)))
        except UntapedError as exc:
            if is_global_github_failure(exc):
                raise
            return CorpusFailure(repo=name, reason=str(exc) or type(exc).__name__, cause=exc)

    def record(_name: str, resolved: tuple[GithubRepo, ...] | CorpusFailure) -> None:
        if isinstance(resolved, CorpusFailure):
            failures.append(resolved)
        else:
            items.update((item.full_name, item) for item in resolved)

    bounded_map(resolve_one, names, concurrency=min(parallel, _API_CONCURRENCY), on_each=record)
    failures.sort(key=lambda failure: failure.repo)
    if names and len(failures) == len(names) and not items:
        detail = "; ".join(failure.reason for failure in failures)
        raise UntapedError(
            f"no requested repository could be resolved: {detail}",
            **attribution(most_severe(failure.cause for failure in failures)),
        )
    rows = (item for _, item in sorted(items.items()) if archived_allows(archived, item.archived))
    return tuple(_target(item) for item in rows), tuple(failures)


def _target(item: GithubRepo) -> CorpusRepoTarget:
    return CorpusRepoTarget(
        full_name=item.full_name,
        default_branch=item.default_branch,
        clone_url=item.clone_url,
        html_url=item.html_url,
        archived=item.archived,
        pushed_at=item.pushed_at,
    )


def _failure(repo: CorpusRepoTarget, exc: Exception) -> CorpusFailure:
    return CorpusFailure(repo=repo.full_name, reason=str(exc) or type(exc).__name__, cause=exc)


def _failure_error(failure: CorpusFailure) -> Failure:
    """The ``error`` of a failed sync row (counted toward the run's exit code)."""
    return Failure.from_exception(failure.cause, message=failure.reason)


class SyncCorpus(_CorpusUseCase):
    """Fetch every repository in scope into the corpus so later sweeps start warm."""

    def __call__(
        self, options: CorpusSyncOptions, *, progress: ProgressHandle = NO_PROGRESS
    ) -> tuple[CorpusSyncOutcome, ...]:
        progress.update(phase="resolving repositories")
        repos, failures = _resolve_online_scope(
            self._inventory,
            scope=options.scope,
            stdin_repos=options.stdin_repos,
            stdin_items=options.stdin_items,
            archived=options.archived,
            parallel=options.parallel,
        )
        outcomes = [
            CorpusSyncOutcome(
                repo=failure.repo,
                action="failed",
                detail=failure.reason,
                error=_failure_error(failure),
            )
            for failure in failures
        ]

        def sync_one(repo: CorpusRepoTarget) -> _ReadyRepo | CorpusFailure:
            return _prepare_repo(
                self._corpus,
                repo,
                selector=options.refs,
                refresh=True if options.refresh else None,
                progress=progress,
                # One repo: its fetch's phases are the line; many: the count is.
                fetch_progress=len(repos) == 1,
                # A failed fetch is the row's failure, never a stale line: no data is served.
                raise_failure=True,
            )

        def record(repo: CorpusRepoTarget, result: _ReadyRepo | CorpusFailure) -> None:
            outcomes.append(_sync_outcome(repo, result))
            done = len(outcomes) - len(failures)
            fetched = sum(outcome.action == "synced" for outcome in outcomes)
            failed = sum(outcome.failed for outcome in outcomes)
            progress.update(
                f"{fetched} fetched, {failed} failed",
                phase="syncing",
                done=done,
                total=len(repos),
            )

        if repos:
            progress.update(phase="syncing", done=0, total=len(repos))
        bounded_map(sync_one, repos, concurrency=options.parallel, on_each=record)
        return tuple(sorted(outcomes, key=lambda outcome: outcome.repo))


def _sync_outcome(repo: CorpusRepoTarget, result: _ReadyRepo | CorpusFailure) -> CorpusSyncOutcome:
    if isinstance(result, CorpusFailure):
        return CorpusSyncOutcome(
            repo=repo.full_name,
            action="failed",
            detail=result.reason,
            error=_failure_error(result),
        )
    if result.refresh_error is not None:
        return CorpusSyncOutcome(
            repo=repo.full_name,
            action="failed",
            fetched_at=result.fetched_at,
            detail=result.refresh_error.reason,
            error=_failure_error(result.refresh_error),
        )
    action = "synced" if result.refreshed else "unchanged" if result.unchanged else "skipped"
    return CorpusSyncOutcome(repo=repo.full_name, action=action, fetched_at=result.fetched_at)


class _Fetched(Filled):
    """What one fill of :data:`SWEEP` did to a repo's stored copy."""

    fetched_at: datetime | None = None
    # GitHub reported no push since the stored fetch, so none ran.
    unchanged: bool = False


def _prepare_repo(
    corpus: GitCorpus,
    repo: CorpusRepoTarget,
    *,
    selector: RefSelector,
    refresh: bool | None,
    progress: ProgressHandle = NO_PROGRESS,
    fetch_progress: bool = False,
    raise_failure: bool = False,
) -> _ReadyRepo | CorpusFailure:
    """Bring one repo's stored copy up to date for ``selector`` through :data:`SWEEP`.

    The copy is the data and :data:`SWEEP` keeps when it was last checked
    (:func:`_sync_freshness`). ``serve()`` refreshes as ``refresh`` asks
    (:func:`_filler`); a failed fetch falls back to the stored copy with one
    ``untaped.stale`` line or, with ``raise_failure``, is the repo's
    ``refresh_error`` without one. A copy that does not cover ``selector``
    was never refreshed for this query: it is fetched whatever its age, and
    when that fails, or the copy still does not cover the query after the
    refresh (another run narrowed it meanwhile), the repo is unscanned.
    ``progress`` is the command's task, which each refresh is a phase of;
    ``fetch_progress`` also hands it to the fetch.
    """
    try:
        stored = corpus.repo_freshness(repo)
        key = corpus.cache_key(repo)
    except (GitCorpusError, OSError) as exc:
        return _failure(repo, exc)
    narrow = refresh is not False and stored is not None and not covers(stored, selector)
    ran: list[str] = []
    failed: list[GitCorpusError] = []
    stopped: list[UntapedError] = []
    fill = _filler(
        corpus,
        repo,
        selector=selector,
        # a copy GitHub reports no push since: its fetch time, kept if the touch fails
        unchanged_since=(
            stored.fetched_at
            if refresh is None and stored and not narrow and unchanged_upstream(stored, repo)
            else None
        ),
        fetch_progress=fetch_progress,
        ran=ran,
        failed=failed,
        stopped=stopped,
    )
    try:
        _sync_freshness(key, stored)
        served = SWEEP.serve(
            {key: repo.full_name},
            refresh=True if narrow else refresh,
            fill=fill,
            read=lambda: None,
            progress=progress,
            # every answer is "empty": a failed refresh raises instead of serving a stale line
            empty=(lambda _: True) if raise_failure or narrow else None,
        )
    except (UntapedError, OSError) as exc:
        _raise_unless_the_repos(stopped)
        remembered = not ran and not isinstance(exc, NeverRefreshed)
        return _not_served(
            repo,
            key,
            # the fetch's own error, not the one serve() rebuilt with a hint to --cached
            failed[-1] if failed else exc,
            remembered=remembered,
            stored=stored if raise_failure else None,
        )
    _raise_unless_the_repos(stopped)
    fetched = served.filled.get(key)
    if fetched is not None:
        return _ReadyRepo(
            repo=repo,
            fetched_at=fetched.fetched_at,
            refreshed=not fetched.unchanged,
            unchanged=fetched.unchanged,
        )
    # served as kept: no refresh due, or one another run made while this one waited
    current = _stored(corpus, repo) if refresh is not False else stored
    if current is not None and refresh is not False and not covers(current, selector):
        return _failure(
            repo, GitCorpusError("another run changed the stored copy's refs; run the sweep again")
        )
    return _ReadyRepo(
        repo=repo, fetched_at=current.fetched_at if current is not None else None, refreshed=False
    )


def _not_served(
    repo: CorpusRepoTarget,
    key: str,
    exc: UntapedError | OSError,
    *,
    remembered: bool,
    stored: CorpusFreshness | None,
) -> _ReadyRepo | CorpusFailure:
    """What a repo ``serve()`` raised for becomes: unscanned, or a stored copy's failed refresh.

    ``stored`` is passed when the stored copy stands beside the failure (``cache
    sync``'s rows). A ``remembered`` failure is one an earlier run left (no
    fill ran): inside the copy's max age it is no refresh this run asked for,
    so the copy is current and its row says so.
    """
    remembered = remembered and isinstance(exc, UntapedError) and exc.details.get("key") == key
    if remembered and stored is not None:
        state = SWEEP.freshness(key)
        if not state.past_max_age(SWEEP.max_age(), now=datetime.now(UTC)):
            return _ReadyRepo(repo=repo, fetched_at=stored.fetched_at, refreshed=False)
    if remembered and isinstance(exc, UntapedError):
        # an earlier run's failed refresh, inside its retry window: no fetch ran
        exc.hint = "pass --refresh to retry it now"
    failure = _failure(repo, exc)
    if stored is None:
        return failure
    return _ReadyRepo(
        repo=repo, fetched_at=stored.fetched_at, refreshed=False, refresh_error=failure
    )


def _filler(
    corpus: GitCorpus,
    repo: CorpusRepoTarget,
    *,
    selector: RefSelector,
    unchanged_since: datetime | None,
    fetch_progress: bool,
    ran: list[str],
    failed: list[GitCorpusError],
    stopped: list[UntapedError],
) -> Callable[[str, ProgressHandle], _Fetched]:
    """The fill of ``repo``'s key: a fetch, or a touch when GitHub reports no push since.

    Each key it starts on goes in ``ran``, each repo's error it raises in
    ``failed``, and each error that is not one repo's (credentials,
    configuration) in ``stopped``.
    """

    def fill(key: str, handle: ProgressHandle) -> _Fetched:
        ran.append(key)
        if unchanged_since is not None:
            try:
                touched = corpus.touch_repo(repo)
            except GitCorpusError, OSError:
                touched = unchanged_since
            return _Fetched(fetched_at=touched, unchanged=True, summary="unchanged")
        try:
            result = corpus.sync_repo(
                repo, selector=selector, progress=handle if fetch_progress else NO_PROGRESS
            )
        except GitCorpusError as exc:
            failed.append(exc)
            raise
        except UntapedError as exc:
            stopped.append(exc)
            raise
        except OSError as exc:
            failed.append(GitCorpusError(str(exc) or type(exc).__name__, system="local"))
            raise failed[-1] from exc
        return _Fetched(fetched_at=_parse_datetime(result.fetched_at))

    return fill


def _raise_unless_the_repos(stopped: list[UntapedError]) -> None:
    """Raise a fill's error that is not one repo's: it stops the run, not the repo.

    ``serve()`` has already remembered it as the key's failed attempt.
    """
    if stopped:
        raise stopped[0]


def _stored(corpus: GitCorpus, repo: CorpusRepoTarget) -> CorpusFreshness | None:
    with contextlib.suppress(GitCorpusError, OSError):
        return corpus.repo_freshness(repo)
    return None


def _sync_freshness(key: str, stored: CorpusFreshness | None) -> None:
    """Bring what :data:`SWEEP` knows of ``key`` in line with the stored copy.

    A copy the cache never saw refreshed was fetched before the cache (or
    by a run that stopped before recording it, or whose first refresh
    failed): it is seeded with its own fetch time. A key known as refreshed
    with no stored copy lost its data (a deleted or released repo), so it is
    forgotten and filled.
    """
    state = SWEEP.freshness(key)
    if stored is None:
        if state.refreshed_at is not None:
            SWEEP.forget(key)
    elif state.refreshed_at is None:
        if state.attempted_at is not None:
            # seed() keeps an existing file: drop the failed first attempt (a fill another
            # run completes meanwhile goes with it, which costs one fetch at worst)
            SWEEP.forget(key)
        SWEEP.seed(key, refreshed_at=stored.fetched_at)


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


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _matching_paths(paths: tuple[str, ...], glob: str) -> tuple[str, ...]:
    return tuple(path for path in paths if fnmatch.fnmatchcase(path, glob))


def _dedupe_matches(matches: Iterable[_ContentMatch]) -> tuple[SweepMatch, ...]:
    # Insertion-ordered dicts give O(1) ref dedupe while keeping first-seen order.
    # Refs showing the same line at the same place collapse into one row.
    grouped: dict[tuple[str, str, int, str], dict[str, None]] = {}
    for match in matches:
        key = (match.repo, match.path, match.line, match.text)
        grouped.setdefault(key, {})[match.ref] = None
    rows = [
        SweepMatch(repo=repo, refs=tuple(refs), path=path, line=line, text=text)
        for (repo, path, line, text), refs in grouped.items()
    ]
    return tuple(sorted(rows, key=lambda row: (row.repo, row.path, row.line, row.text)))
