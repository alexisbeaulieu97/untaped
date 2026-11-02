"""Application use cases for repository sweep queries."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from untaped.sdk import ConfigError, UsageError, bounded_map
from untaped.sdk.storage import NO_PROGRESS, ProgressHandle
from untaped_github.application.corpus_repos import (
    CorpusUseCase,
    ReadyRepo,
    failure_of,
    prepare_repo,
    resolve_online_scope,
)
from untaped_github.application.inventory import RepositoryInventoryScope
from untaped_github.application.ports import GitCorpus
from untaped_github.domain import (
    CODEOWNERS_LOCATIONS,
    ArchivedMode,
    CorpusFailure,
    CorpusRepoTarget,
    GrepHit,
    GrepSpec,
    RefEvaluation,
    SweepQuery,
    archived_allows,
    parse_codeowners,
    ref_display_names,
    ref_matches,
)
from untaped_github.errors import GitCorpusError
from untaped_github.records import Repo, SweepMatch, SweepRepo


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
    stdin_items: tuple[Repo, ...] = ()


@dataclass(frozen=True)
class SweepReport:
    """Result of a repository sweep."""

    rows: tuple[SweepRepo, ...]
    matches: tuple[SweepMatch, ...]
    unscanned: tuple[CorpusFailure, ...]
    scanned: int
    refreshed: int
    cached: int
    oldest_fetched_at: datetime | None


@dataclass(frozen=True)
class _RepoScan:
    outcome: SweepRepo | None
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


class Sweep(CorpusUseCase):
    """Run a sweep query across repository inventory and local corpus refs."""

    def __call__(
        self, options: SweepOptions, *, progress: ProgressHandle = NO_PROGRESS
    ) -> SweepReport:
        options.query.validate()
        progress.update(phase="resolving repositories")
        repos, scope_failures = self._resolve_scope(options)

        ready: list[ReadyRepo] = []
        scans: list[_RepoScan] = []
        failures: list[CorpusFailure] = []
        counts = {"fetched": 0, "failed": 0}

        # Sync and scan run in one worker per repo, so scanning starts as soon
        # as a repo's fetch ends instead of after the slowest fetch.
        def sweep_one(
            repo: CorpusRepoTarget,
        ) -> tuple[ReadyRepo | None, _RepoScan | CorpusFailure]:
            prepared = prepare_repo(
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
                return prepared, failure_of(repo, exc)

        def record(
            _repo: CorpusRepoTarget,
            outcome: tuple[ReadyRepo | None, _RepoScan | CorpusFailure],
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

        return resolve_online_scope(
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

    def _scan_repo(self, ready: ReadyRepo, options: SweepOptions) -> _RepoScan:
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
            outcome=SweepRepo(
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
                fetched_at=ready.fetched_at,
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
