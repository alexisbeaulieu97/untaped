"""Application use case for repository sweep queries."""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from untaped.api import ConfigError, bounded_map
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
    GrepHit,
    RefEvaluation,
    RefSelector,
    RepoSweepOutcome,
    SweepQuery,
    covers,
    parse_codeowners,
    ref_matches,
)
from untaped.capabilities.github.domain.errors import GitCorpusError

InventoryResolver = Callable[[RepositoryInventoryScope], tuple[RepositoryInventoryItem, ...]]
AuthHeaderSupplier = Callable[[], str | None]


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

    def __post_init__(self) -> None:
        if self.depth < 0:
            raise ValueError("depth must be non-negative")
        if self.parallel < 1:
            raise ValueError("parallel must be positive")
        if self.max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")


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


@dataclass(frozen=True)
class _ReadyRepo:
    repo: CorpusRepoTarget
    fetched_at: datetime | None
    refreshed: bool


@dataclass(frozen=True)
class _RepoScan:
    outcome: RepoSweepOutcome | None
    matches: tuple[_ContentMatch, ...]


@dataclass(frozen=True)
class _RefScan:
    evaluation: RefEvaluation
    owner_paths: tuple[str, ...]
    matches: tuple[_ContentMatch, ...]


@dataclass(frozen=True)
class _ContentMatch:
    full_name: str
    ref: str
    blob_oid: str
    path: str
    line: int
    text: str


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

    def __call__(self, options: SweepOptions) -> SweepReport:
        options.query.validate()
        repos = self._resolve_scope(options)
        ready, prep_failures = self._prepare_repos(repos, options)

        scans: list[_RepoScan] = []
        scan_failures: list[CorpusFailure] = []

        def scan_one(ready_repo: _ReadyRepo) -> _RepoScan | CorpusFailure:
            try:
                return self._scan_repo(ready_repo, options)
            except GitCorpusError as exc:
                return CorpusFailure(repo=ready_repo.repo.full_name, reason=str(exc))

        def record(_ready_repo: _ReadyRepo, outcome: _RepoScan | CorpusFailure) -> None:
            if isinstance(outcome, CorpusFailure):
                scan_failures.append(outcome)
            else:
                scans.append(outcome)

        bounded_map(scan_one, ready, concurrency=options.parallel, on_each=record)
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
            unscanned=(*prep_failures, *scan_failures),
            scanned=len(scans),
            refreshed=refreshed,
            cached=cached,
            oldest_fetched_at=min(scanned_dates) if scanned_dates else None,
        )

    def _resolve_scope(self, options: SweepOptions) -> tuple[CorpusRepoTarget, ...]:
        if options.sync == "off":
            return self._resolve_offline_scope(options)

        repos = tuple(dict.fromkeys((*options.scope.repos, *options.stdin_repos)))
        scope = RepositoryInventoryScope(
            orgs=options.scope.orgs,
            teams=options.scope.teams,
            repos=repos,
        )
        rows = (
            item for item in self._inventory(scope) if options.include_archived or not item.archived
        )
        return tuple(_target(item) for item in rows)

    def _resolve_offline_scope(self, options: SweepOptions) -> tuple[CorpusRepoTarget, ...]:
        if options.scope.teams:
            raise ConfigError("--team requires the API and cannot resolve offline")
        names = set((*options.scope.repos, *options.stdin_repos))
        rows = self._corpus.list_repos(root=self._root)
        targets: list[CorpusRepoTarget] = []
        for row in rows:
            if not options.include_archived and row.archived:
                continue
            if options.scope.orgs and not any(
                row.repo.startswith(f"{org}/") for org in options.scope.orgs
            ):
                continue
            if names and row.repo not in names:
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
            raise ConfigError("corpus has no repos in scope; run without --no-sync to populate")
        return tuple(sorted(targets, key=lambda repo: repo.full_name))

    def _prepare_repos(
        self,
        repos: tuple[CorpusRepoTarget, ...],
        options: SweepOptions,
    ) -> tuple[tuple[_ReadyRepo, ...], tuple[CorpusFailure, ...]]:
        if options.sync == "off":
            return tuple(
                _ReadyRepo(
                    repo=repo,
                    fetched_at=_freshness_datetime(
                        self._corpus.repo_freshness(repo, root=self._root)
                    ),
                    refreshed=False,
                )
                for repo in repos
            ), ()

        ready: list[_ReadyRepo] = []
        failures: list[CorpusFailure] = []

        def prepare_one(repo: CorpusRepoTarget) -> _ReadyRepo | CorpusFailure:
            freshness = self._corpus.repo_freshness(repo, root=self._root)
            must_refresh = options.sync == "force" or _needs_refresh(
                freshness,
                selector=options.query.refs,
                max_age_seconds=options.max_age_seconds,
            )
            if not must_refresh:
                return _ReadyRepo(
                    repo=repo, fetched_at=_freshness_datetime(freshness), refreshed=False
                )
            try:
                result = self._corpus.sync_repo(
                    repo,
                    root=self._root,
                    selector=options.query.refs,
                    depth=options.depth,
                    auth_header=self._auth_header(),
                )
            except GitCorpusError as exc:
                if freshness is not None and covers(freshness, options.query.refs):
                    return _ReadyRepo(repo=repo, fetched_at=freshness.fetched_at, refreshed=False)
                return CorpusFailure(repo=repo.full_name, reason=str(exc))
            return _ReadyRepo(
                repo=repo,
                fetched_at=_parse_datetime(result.fetched_at),
                refreshed=True,
            )

        def record(_repo: CorpusRepoTarget, outcome: _ReadyRepo | CorpusFailure) -> None:
            if isinstance(outcome, CorpusFailure):
                failures.append(outcome)
            else:
                ready.append(outcome)

        bounded_map(prepare_one, repos, concurrency=options.parallel, on_each=record)
        return tuple(ready), tuple(failures)

    def _scan_repo(self, ready: _ReadyRepo, options: SweepOptions) -> _RepoScan:
        refs_matched: list[str] = []
        aggregate_hits: dict[str, int] = {}
        owner_paths: set[str] = set()
        matches: list[_ContentMatch] = []
        for ref in self._corpus.local_refs(
            ready.repo, root=self._root, selector=options.query.refs
        ):
            ref_scan = self._scan_ref(ready.repo, ref, options.query)
            if not ref_matches(options.query, ref_scan.evaluation):
                continue
            refs_matched.append(ref)
            owner_paths.update(ref_scan.owner_paths)
            matches.extend(ref_scan.matches)
            for label, count in ref_scan.evaluation.hits.items():
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

    def _scan_ref(self, repo: CorpusRepoTarget, ref: str, query: SweepQuery) -> _RefScan:
        hits: dict[str, int] = {}
        owner_paths: set[str] = set()
        matches: list[_ContentMatch] = []
        for pattern in query.greps:
            label = f"grep:{pattern}"
            grep_hits = self._grep(repo, ref, pattern, query)
            hits[label] = len(grep_hits)
            owner_paths.update(hit.path for hit in grep_hits)
            matches.extend(_content_match(repo.full_name, ref, hit) for hit in grep_hits)
        for pattern in query.not_greps:
            label = f"not-grep:{pattern}"
            hits[label] = len(self._grep(repo, ref, pattern, query))

        tree: tuple[str, ...] | None = None
        for glob in query.has_files:
            tree = self._tree(repo, ref) if tree is None else tree
            matched = _matching_paths(tree, glob)
            hits[f"has-file:{glob}"] = 1 if matched else 0
            owner_paths.update(matched)
        for glob in query.lacks_files:
            tree = self._tree(repo, ref) if tree is None else tree
            hits[f"lacks-file:{glob}"] = 1 if _matching_paths(tree, glob) else 0

        return _RefScan(
            evaluation=RefEvaluation(ref=ref, hits=hits),
            owner_paths=tuple(sorted(owner_paths)),
            matches=tuple(matches),
        )

    def _grep(
        self,
        repo: CorpusRepoTarget,
        ref: str,
        pattern: str,
        query: SweepQuery,
    ) -> tuple[GrepHit, ...]:
        return self._corpus.grep_ref(
            repo,
            root=self._root,
            ref=ref,
            pattern=pattern,
            paths=query.paths,
            ignore_case=query.ignore_case,
            fixed_strings=query.fixed_strings,
            word_regexp=query.word_regexp,
        )

    def _tree(self, repo: CorpusRepoTarget, ref: str) -> tuple[str, ...]:
        return self._corpus.tree_paths(repo, root=self._root, ref=ref)

    def _owners_for(self, repo: CorpusRepoTarget, *, paths: Iterable[str]) -> tuple[str, ...]:
        branch = repo.default_branch
        if not branch:
            return ()
        rules = None
        for path in CODEOWNERS_LOCATIONS:
            try:
                text = self._corpus.read_blob(repo, root=self._root, ref=branch, path=path)
            except GitCorpusError:
                return ()
            if text is None:
                continue
            try:
                rules = parse_codeowners(text)
            except Exception:
                return ()
            break
        if rules is None:
            return ()
        owner_rows: list[str] = []
        sorted_paths = tuple(sorted(paths))
        if not sorted_paths:
            owner_rows.extend(rules.default_owners())
        else:
            for path in sorted_paths:
                owner_rows.extend(rules.owners_for(path))
        return tuple(dict.fromkeys(owner_rows))


def _target(item: RepositoryInventoryItem) -> CorpusRepoTarget:
    return CorpusRepoTarget(
        full_name=item.full_name,
        default_branch=item.default_branch,
        clone_url=item.clone_url,
        html_url=item.html_url,
        archived=item.archived,
    )


def _needs_refresh(
    freshness: CorpusFreshness | None,
    *,
    selector: RefSelector,
    max_age_seconds: int,
) -> bool:
    if freshness is None:
        return True
    if not covers(freshness, selector):
        return True
    return (datetime.now(UTC) - freshness.fetched_at).total_seconds() > max_age_seconds


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
        blob_oid=hit.blob_oid,
        path=hit.path,
        line=hit.line,
        text=hit.text,
    )


def _dedupe_matches(matches: Iterable[_ContentMatch]) -> tuple[SweepMatch, ...]:
    grouped: dict[tuple[str, str, str, int, str], list[str]] = {}
    for match in matches:
        key = (match.full_name, match.blob_oid, match.path, match.line, match.text)
        grouped.setdefault(key, [])
        if match.ref not in grouped[key]:
            grouped[key].append(match.ref)
    rows = [
        SweepMatch(
            full_name=full_name,
            refs=tuple(refs),
            path=path,
            line=line,
            text=text,
        )
        for (full_name, _blob_oid, path, line, text), refs in grouped.items()
    ]
    return tuple(sorted(rows, key=lambda row: (row.full_name, row.path, row.line, row.text)))
