"""SQLite-backed source index for reverse-impact queries."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import batched
from pathlib import Path
from threading import Lock

from untaped.sdk import Diagnostics
from untaped_ansible.domain.identity import repo_key
from untaped_ansible.domain.payloads import (
    CachedRef,
    IndexedDependency,
    RefScan,
    RefScanMetadata,
    RefScanTouch,
    SourceIndexStatus,
    SourceRepoMetadata,
)
from untaped_ansible.domain.variable_models import (
    AnalysedFile,
    CommitFacts,
    CommitListing,
    How,
    VariableFact,
)
from untaped_ansible.errors import DependencyIndexError
from untaped_ansible.infrastructure.sqlite_rows import (
    dump_dt,
    edge_from_row,
    load_dt,
    ref_scan_from_row,
)
from untaped_ansible.infrastructure.sqlite_schema import ensure_schema
from untaped_ansible.settings import INDEX_DIR

_diag = Diagnostics()


def open_index() -> SqliteDependencyIndex:
    """The dependency index of the active settings: the one way the plugin opens it.

    The file is the index place's own layout (``index.v4.sqlite3`` in
    ``ansible.index_dir``, else in ``plugins/ansible/index/``), held in use
    while the process runs.
    """
    return SqliteDependencyIndex(INDEX_DIR.open())


class SqliteDependencyIndex:
    """SQLite adapter satisfying the graph read port."""

    def __init__(self, path: Path, *, warn: Callable[[str], None] | None = None) -> None:
        """``warn`` hears that an outdated index was rebuilt empty."""
        self._path = path.expanduser()
        self._warn = warn or _diag.warning
        self._schema_lock = Lock()
        self._schema_ready = False
        self._parent_ready = False

    def ref_scans(
        self,
        source_key: str,
        source_repo: str,
        refs: Iterable[tuple[str, str]],
    ) -> dict[tuple[str, str], RefScanMetadata]:
        requested = sorted(set(refs))
        if not requested:
            return {}
        scans: dict[tuple[str, str], RefScanMetadata] = {}
        with self._db() as db:
            for chunk in batched(requested, 400, strict=False):
                placeholders = ",".join("(?, ?)" for _ in chunk)
                params: list[object] = [
                    value for ref_kind, source_ref in chunk for value in (ref_kind, source_ref)
                ]
                params.extend((source_key, source_repo))
                rows = db.execute(
                    f"""
                    with requested(ref_kind, source_ref) as (
                        values {placeholders}
                    )
                    select scans.source_key, scans.source_repo, scans.ref_kind, scans.source_ref,
                           scans.source_sha, scans.clone_url, scans.clone_protocol,
                           scans.dependency_paths_fingerprint, scans.aliases_fingerprint,
                           scans.checked_at, scans.indexed_at
                    from source_ref_scans as scans
                    join requested
                      on requested.ref_kind = scans.ref_kind
                     and requested.source_ref = scans.source_ref
                    where scans.source_key = ? and scans.source_repo = ?
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    key = (str(row["ref_kind"]), str(row["source_ref"]))
                    scans[key] = ref_scan_from_row(row)
        return scans

    def commit_source_ref_partial_refresh(
        self,
        source_key: str,
        *,
        scans: tuple[RefScan, ...],
        touches: tuple[RefScanTouch, ...],
        keep: set[tuple[str, str, str]],
        repo_metadata: tuple[SourceRepoMetadata, ...] = (),
        processed_repos: frozenset[str] = frozenset(),
        source_fingerprint: str | None = None,
        progress_statuses: dict[str, str] | None = None,
        analysed: tuple[AnalysedFile, ...] = (),
        listings: tuple[CommitListing, ...] = (),
        analyser: str | None = None,
    ) -> None:
        """Commit processed repos without marking the whole source fresh.

        ``analysed`` files and the commits' ``listings`` are the variable
        analysis made at ``analyser``, written in the same transaction as the
        scans, so a commit is never marked analysed without its scan.
        """
        if progress_statuses and source_fingerprint is None:
            raise ValueError("source_fingerprint is required when progress_statuses are provided")
        if (analysed or listings) and analyser is None:
            raise ValueError("analyser is required when an analysis is recorded")
        with self._db() as db:
            _replace_ref_scans(db, scans)
            _touch_ref_scans(db, touches)
            if analyser is not None:
                _record_analysis(db, analysed, listings, analyser=analyser)
            _prune_source_refs_for_repos(db, source_key, keep, processed_repos)
            _upsert_source_repo_metadata(db, repo_metadata)
            if source_fingerprint is not None and progress_statuses:
                _mark_refresh_progress(db, source_key, source_fingerprint, progress_statuses)
            _delete_orphan_snapshots(db)

    def complete_source_ref_refresh(
        self,
        source_key: str,
        *,
        source_repos: frozenset[str],
        scanned_at: datetime,
    ) -> None:
        """Mark a source refresh complete and prune repos outside the expanded source."""
        with self._db() as db:
            _prune_source_refs_to_repos(db, source_key, source_repos)
            _prune_source_repo_metadata_to_repos(db, source_key, source_repos)
            _refresh_source_run_from_ref_scans(db, source_key, scanned_at=scanned_at)
            db.execute("delete from source_refresh_progress where source_key = ?", (source_key,))
            _delete_orphan_snapshots(db)

    def refresh_progress(
        self,
        source_key: str,
        source_fingerprint: str,
    ) -> dict[str, str]:
        """Return processed repo statuses for the current refresh fingerprint."""
        with self._db() as db:
            db.execute(
                """
                delete from source_refresh_progress
                where source_key = ? and source_fingerprint != ?
                """,
                (source_key, source_fingerprint),
            )
            rows = db.execute(
                """
                select source_repo, status
                from source_refresh_progress
                where source_key = ? and source_fingerprint = ?
                """,
                (source_key, source_fingerprint),
            ).fetchall()
        return {str(row["source_repo"]): str(row["status"]) for row in rows}

    def clear_refresh_progress(self, source_key: str) -> None:
        """Clear resumable refresh state for a source."""
        with self._db() as db:
            db.execute("delete from source_refresh_progress where source_key = ?", (source_key,))

    def commits_needing_analysis(
        self, repo: str, shas: Collection[str], *, analyser: str
    ) -> set[str]:
        """The commits of ``shas`` with no listing recorded at ``analyser``."""
        wanted = sorted(set(shas))
        done: set[str] = set()
        with self._db() as db:
            for chunk in batched(wanted, 500, strict=False):
                marks = ",".join("?" for _ in chunk)
                rows = db.execute(
                    f"""
                    select source_sha from variable_commits
                    where source_repo_key = ? and analyser = ? and source_sha in ({marks})
                    """,
                    (repo_key(repo), analyser, *chunk),
                ).fetchall()
                done.update(str(row["source_sha"]) for row in rows)
        return set(wanted) - done

    def analysed_files(
        self, files: Collection[tuple[str, str]], *, analyser: str
    ) -> set[tuple[str, str]]:
        """The ``(blob, path)`` pairs of ``files`` already analysed at ``analyser``."""
        wanted = sorted(set(files))
        found: set[tuple[str, str]] = set()
        with self._db() as db:
            for chunk in batched(wanted, 400, strict=False):
                values = ",".join("(?, ?)" for _ in chunk)
                rows = db.execute(
                    f"""
                    with requested(blob, path) as (values {values})
                    select files.blob, files.path from variable_files as files
                    join requested on requested.blob = files.blob and requested.path = files.path
                    where files.analyser = ?
                    """,
                    [*(value for pair in chunk for value in pair), analyser],
                ).fetchall()
                found.update((str(row["blob"]), str(row["path"])) for row in rows)
        return found

    def variable_facts(
        self, commits: Collection[tuple[str, str]], names: Collection[str]
    ) -> CommitFacts:
        """The facts naming any of ``names`` at each ``(repo, sha)`` of ``commits``.

        A commit counts as analysed when any analysis recorded its listing,
        at this analyser version or an older one (``--cached`` serves an
        outdated analysis; a refresh replaces it).
        """
        wanted = sorted(set(commits))
        by_key: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for repo, sha in wanted:
            by_key.setdefault((repo_key(repo), sha), []).append((repo, sha))
        facts: dict[tuple[str, str], list[VariableFact]] = {}
        skipped: dict[tuple[str, str], list[tuple[str, str]]] = {}
        analysed: set[tuple[str, str]] = set()
        asked = sorted(set(names))
        with self._db() as db:
            for chunk in batched(list(by_key), 400, strict=False):
                values = ",".join("(?, ?)" for _ in chunk)
                params = [value for pair in chunk for value in pair]
                requested = f"with requested(repo, sha) as (values {values})"
                for row in db.execute(
                    f"""
                    {requested}
                    select requested.repo, requested.sha from requested
                    join variable_commits as commits
                      on commits.source_repo_key = requested.repo
                     and commits.source_sha = requested.sha
                    """,
                    params,
                ):
                    analysed.add((str(row["repo"]), str(row["sha"])))
                for row in db.execute(
                    f"""
                    {requested}
                    select requested.repo, requested.sha, files.path, files.skipped
                    from requested
                    join variable_listing as listing
                      on listing.source_repo_key = requested.repo
                     and listing.source_sha = requested.sha
                    join variable_files as files on files.id = listing.file_id
                    where files.skipped is not null
                    order by files.path
                    """,
                    params,
                ):
                    for commit in by_key[(str(row["repo"]), str(row["sha"]))]:
                        skipped.setdefault(commit, []).append(
                            (str(row["path"]), str(row["skipped"]))
                        )
                if not asked:
                    continue
                marks = ",".join("?" for _ in asked)
                for row in db.execute(
                    f"""
                    {requested}
                    select requested.repo, requested.sha, files.path,
                           facts.name, facts.how, facts.line, facts.key_path
                    from requested
                    cross join variable_listing as listing
                      on listing.source_repo_key = requested.repo
                     and listing.source_sha = requested.sha
                    cross join variable_files as files on files.id = listing.file_id
                    cross join variable_facts as facts on facts.file_id = files.id
                    where facts.name in ({marks})
                    order by files.path, facts.line, facts.rowid
                    """,
                    [*params, *asked],
                ):
                    fact = VariableFact(
                        name=str(row["name"]),
                        file=str(row["path"]),
                        line=int(row["line"]),
                        key_path=json.loads(row["key_path"]),
                        how=_HOWS[int(row["how"])],
                    )
                    for commit in by_key[(str(row["repo"]), str(row["sha"]))]:
                        facts.setdefault(commit, []).append(fact)
        unanalysed = {
            commit for key, asked_as in by_key.items() if key not in analysed for commit in asked_as
        }
        return CommitFacts(facts=facts, unanalysed=frozenset(unanalysed), skipped=skipped)

    def ref_shas(self, source_keys: Collection[str], repo: str, ref: str | None) -> set[str]:
        """The commits ``repo`` is indexed at under ``source_keys``: at ``ref``, else every ref."""
        keys = sorted(set(source_keys))
        if not keys:
            return set()
        marks = ",".join("?" for _ in keys)
        ref_clause = "" if ref is None else "and source_ref = ?"
        with self._db() as db:
            rows = db.execute(
                f"""
                select distinct source_sha from source_ref_scans
                where source_key in ({marks}) and source_repo_key = ? {ref_clause}
                """,
                [*keys, repo_key(repo), *([] if ref is None else [ref])],
            ).fetchall()
        return {str(row["source_sha"]) for row in rows if row["source_sha"]}

    def dependencies(
        self,
        repo: str,
        ref: str | None,
        *,
        source_key: str | None,
    ) -> list[IndexedDependency]:
        # Batch-of-one keeps single/batch read parity by construction.
        return self.dependencies_batch([(repo, ref)], source_key=source_key)[(repo, ref)]

    def dependents(
        self,
        repo: str,
        ref: str | None,
        *,
        source_key: str | None,
    ) -> list[IndexedDependency]:
        # Batch-of-one keeps single/batch read parity by construction.
        return self.dependents_batch([(repo, ref)], source_key=source_key)[(repo, ref)]

    def dependencies_batch(
        self,
        pairs: Sequence[tuple[str, str | None]],
        *,
        source_key: str | None,
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        return self._select_edges_batch(
            pairs,
            source_key=source_key,
            join_sql="""
                join source_ref_scans as scans
                  on scans.source_repo_key = requested.repo
                 and (requested.ref is null or scans.source_ref = requested.ref)
                join snapshot_edges as edges
                  on edges.snapshot_id = scans.snapshot_id
            """,
        )

    def dependents_batch(
        self,
        pairs: Sequence[tuple[str, str | None]],
        *,
        source_key: str | None,
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        # An unpinned declaration installs the dependency's default branch, so
        # it matches a requested ref equal to that repo's cached default branch.
        return self._select_edges_batch(
            pairs,
            source_key=source_key,
            join_sql="""
                join snapshot_edges as edges
                  on edges.dependency_repo_key = requested.repo
                 and (
                     requested.ref is null
                     or edges.dependency_version = requested.ref
                     or edges.dependency_version is null
                 )
                join source_ref_scans as scans
                  on scans.snapshot_id = edges.snapshot_id
                 and (
                     requested.ref is null
                     or edges.dependency_version is not null
                     or exists (
                         select 1
                         from source_repo_metadata as dependency_metadata
                         where dependency_metadata.source_key = scans.source_key
                           and dependency_metadata.source_repo_key
                               = edges.dependency_repo_key
                           and dependency_metadata.default_branch = requested.ref
                     )
                 )
            """,
        )

    def cached_refs(self, repo: str, *, source_key: str | None) -> set[str]:
        if source_key is None:
            return set()
        with self._db() as db:
            rows = db.execute(
                """
                select source_ref
                from source_ref_scans
                where source_key = ? and source_repo_key = ? and source_ref != ''
                """,
                (source_key, repo_key(repo)),
            ).fetchall()
        return {str(row["source_ref"]) for row in rows}

    def cached_ref_metadata(self, repo: str, *, source_key: str | None) -> tuple[CachedRef, ...]:
        # Batch-of-one keeps single/batch read parity by construction.
        return self.cached_ref_metadata_batch([repo], source_key=source_key)[repo]

    def cached_ref_metadata_batch(
        self,
        repos: Sequence[str],
        *,
        source_key: str | None,
    ) -> dict[str, tuple[CachedRef, ...]]:
        requested = list(dict.fromkeys(repos))
        if source_key is None or not requested:
            return dict.fromkeys(requested, ())
        grouped: dict[str, dict[tuple[str, str | None], CachedRef]] = {
            repo: {} for repo in requested
        }
        by_key: dict[str, list[str]] = {}
        for repo in requested:
            by_key.setdefault(repo_key(repo), []).append(repo)
        with self._db() as db:
            for chunk in batched(list(by_key), 400, strict=False):
                placeholders = ",".join("(?)" for _ in chunk)
                params: list[object] = [*chunk, source_key]
                rows = db.execute(
                    f"""
                    with requested(repo) as (
                        values {placeholders}
                    )
                    select requested.repo as requested_repo, scans.source_ref as name,
                           nullif(scans.ref_kind, '') as kind,
                           metadata.default_branch
                    from requested
                    join source_ref_scans as scans
                      on scans.source_repo_key = requested.repo
                    left join source_repo_metadata as metadata
                      on metadata.source_key = scans.source_key
                     and metadata.source_repo = scans.source_repo
                    where scans.source_key = ? and scans.source_ref != ''
                    order by scans.id
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    key = (str(row["name"]), _optional_str(row["kind"]))
                    for repo in by_key[str(row["requested_repo"])]:
                        grouped[repo].setdefault(
                            key,
                            CachedRef(
                                name=key[0],
                                kind=key[1],
                                default_branch=_optional_str(row["default_branch"]),
                            ),
                        )
        return {repo: tuple(refs.values()) for repo, refs in grouped.items()}

    def status(self, source_key: str) -> SourceIndexStatus | None:
        with self._db() as db:
            row = db.execute(
                "select scanned_at, repos, refs, edges from source_runs where source_key = ?",
                (source_key,),
            ).fetchone()
            if row is None:
                return None
        return SourceIndexStatus(
            source_key=source_key,
            scanned_at=load_dt(row["scanned_at"]),
            repos=int(row["repos"]),
            refs=int(row["refs"]),
            edges=int(row["edges"]),
        )

    def source_repos(self, source_key: str) -> dict[str, tuple[str, ...]]:
        """The repos ``source_key``'s last refresh selected, ``OWNER/NAME`` → clone URLs it used.

        A repo with no scanned ref has no URL: its refresh never fetched it.
        """
        with self._db() as db:
            rows = db.execute(
                """
                select source_repo, null as clone_url
                from source_repo_metadata where source_key = ?
                union
                select source_repo, clone_url
                from source_ref_scans where source_key = ?
                """,
                (source_key, source_key),
            ).fetchall()
        urls: dict[str, set[str]] = {}
        for row in rows:
            found = urls.setdefault(str(row["source_repo"]), set())
            if row["clone_url"]:
                found.add(str(row["clone_url"]))
        return {repo: tuple(sorted(found)) for repo, found in sorted(urls.items())}

    def selecting_sources(self, source_keys: Iterable[str]) -> dict[str, set[str]]:
        """Which of ``source_keys`` selected each repo in their last refresh, by repo key."""
        keys = sorted(set(source_keys))
        selected: dict[str, set[str]] = {}
        if not keys:
            return selected
        with self._db() as db:
            for chunk in batched(keys, 400, strict=False):
                marks = ",".join("?" for _ in chunk)
                rows = db.execute(
                    f"""
                    select source_key, source_repo_key from source_repo_metadata
                    where source_key in ({marks})
                    union
                    select source_key, source_repo_key from source_ref_scans
                    where source_key in ({marks})
                    """,
                    [*chunk, *chunk],
                ).fetchall()
                for row in rows:
                    selected.setdefault(str(row["source_repo_key"]), set()).add(
                        str(row["source_key"])
                    )
        return selected

    def clear(self, source_key: str) -> None:
        with self._db() as db:
            db.execute("delete from source_runs where source_key = ?", (source_key,))
            db.execute("delete from source_ref_scans where source_key = ?", (source_key,))
            db.execute("delete from source_repo_metadata where source_key = ?", (source_key,))
            db.execute("delete from source_refresh_progress where source_key = ?", (source_key,))
            _delete_orphan_snapshots(db)

    def _select_edges_batch(
        self,
        pairs: Sequence[tuple[str, str | None]],
        *,
        source_key: str | None,
        join_sql: str,
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        requested = list(dict.fromkeys(pairs))
        results: dict[tuple[str, str | None], list[IndexedDependency]] = {
            pair: [] for pair in requested
        }
        if not requested:
            return results
        key_clause = "" if source_key is None else "where scans.source_key = ?"
        by_key: dict[tuple[str, str | None], list[tuple[str, str | None]]] = {}
        for repo, ref in requested:
            by_key.setdefault((repo_key(repo), ref), []).append((repo, ref))
        with self._db() as db:
            # 400 pairs x 2 columns = 800 bound params, under the pre-3.32
            # limit of 999 (matching the ref_scans VALUES chunking above).
            for chunk in batched(list(by_key), 400, strict=False):
                placeholders = ",".join("(?, ?)" for _ in chunk)
                params: list[object] = [value for pair in chunk for value in pair]
                if source_key is not None:
                    params.append(source_key)
                rows = db.execute(
                    f"""
                    with requested(repo, ref) as (
                        values {placeholders}
                    )
                    select requested.repo as requested_repo,
                           requested.ref as requested_ref,
                           scans.source_repo,
                           nullif(scans.source_ref, '') as source_ref,
                           nullif(scans.ref_kind, '') as source_ref_kind,
                           nullif(scans.source_sha, '') as source_sha,
                           edges.dependency_repo, edges.dependency_name,
                           edges.dependency_version, edges.source_path, edges.unresolved
                    from requested
                    {join_sql}
                    {key_clause}
                    order by scans.id, edges.id
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    requested_ref = row["requested_ref"]
                    key = (
                        str(row["requested_repo"]),
                        str(requested_ref) if requested_ref is not None else None,
                    )
                    edge = edge_from_row(row)
                    for pair in by_key[key]:
                        results[pair].append(edge)
        return results

    def _connect(self) -> sqlite3.Connection:
        if not self._parent_ready:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DependencyIndexError(
                    f"cannot create index directory {self._path.parent}: {exc}"
                ) from exc
            self._parent_ready = True
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        return db

    def _ensure_schema(self, db: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                ensure_schema(db, self._path, warn=self._warn)
                self._schema_ready = True

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        try:
            db = self._connect()
        except sqlite3.Error as exc:
            raise DependencyIndexError(f"cannot open index {self._path}: {exc}") from exc
        try:
            with db:
                self._ensure_schema(db)
                yield db
        except sqlite3.Error as exc:
            raise DependencyIndexError(f"index {self._path} failed: {exc}") from exc
        finally:
            db.close()


_SnapshotIdentity = tuple[str, str, str, str]


def _snapshot_identity(scan: RefScan) -> _SnapshotIdentity:
    return (
        scan.source_repo,
        scan.source_sha,
        scan.dependency_paths_fingerprint,
        scan.aliases_fingerprint,
    )


def _replace_ref_scans(db: sqlite3.Connection, scans: tuple[RefScan, ...]) -> None:
    if not scans:
        return
    snapshot_ids = _existing_snapshot_ids(db, scans)
    for scan in scans:
        identity = _snapshot_identity(scan)
        if identity not in snapshot_ids:
            snapshot_ids[identity] = _insert_snapshot(db, scan)
    db.executemany(
        """
        delete from source_ref_scans
        where source_key = ? and source_repo = ? and ref_kind = ? and source_ref = ?
        """,
        [(scan.source_key, scan.source_repo, scan.ref_kind, scan.source_ref) for scan in scans],
    )
    db.executemany(
        """
        insert into source_ref_scans(
            source_key, source_repo, source_repo_key, ref_kind, source_ref, source_sha,
            clone_url, clone_protocol, dependency_paths_fingerprint,
            aliases_fingerprint, checked_at, indexed_at, snapshot_id
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                scan.source_key,
                scan.source_repo,
                repo_key(scan.source_repo),
                scan.ref_kind,
                scan.source_ref,
                scan.source_sha,
                scan.clone_url,
                scan.clone_protocol,
                scan.dependency_paths_fingerprint,
                scan.aliases_fingerprint,
                dump_dt(scan.checked_at),
                dump_dt(scan.indexed_at),
                snapshot_ids[_snapshot_identity(scan)],
            )
            for scan in scans
        ],
    )


def _existing_snapshot_ids(
    db: sqlite3.Connection,
    scans: tuple[RefScan, ...],
) -> dict[_SnapshotIdentity, int]:
    identities = sorted({_snapshot_identity(scan) for scan in scans})
    ids: dict[_SnapshotIdentity, int] = {}
    # 200 identities x 4 columns = 800 bound params, under the pre-3.32 limit of
    # 999, even though this module already assumes SQLite >= 3.35 (RETURNING).
    for chunk in batched(identities, 200, strict=False):
        placeholders = ",".join("(?, ?, ?, ?)" for _ in chunk)
        params = [value for identity in chunk for value in identity]
        rows = db.execute(
            f"""
            with requested(
                source_repo, source_sha, dependency_paths_fingerprint, aliases_fingerprint
            ) as (
                values {placeholders}
            )
            select snapshots.id, snapshots.source_repo, snapshots.source_sha,
                   snapshots.dependency_paths_fingerprint, snapshots.aliases_fingerprint
            from dependency_snapshots as snapshots
            join requested
              on requested.source_repo = snapshots.source_repo
             and requested.source_sha = snapshots.source_sha
             and requested.dependency_paths_fingerprint
                 = snapshots.dependency_paths_fingerprint
             and requested.aliases_fingerprint = snapshots.aliases_fingerprint
            """,
            params,
        ).fetchall()
        for row in rows:
            identity = (
                str(row["source_repo"]),
                str(row["source_sha"]),
                str(row["dependency_paths_fingerprint"]),
                str(row["aliases_fingerprint"]),
            )
            ids[identity] = int(row["id"])
    return ids


def _insert_snapshot(db: sqlite3.Connection, scan: RefScan) -> int:
    """Insert a new dependency snapshot with its edges and return its id."""
    row = db.execute(
        """
        insert into dependency_snapshots(
            source_repo, source_sha, dependency_paths_fingerprint, aliases_fingerprint
        ) values (?, ?, ?, ?)
        on conflict(source_repo, source_sha, dependency_paths_fingerprint, aliases_fingerprint)
        -- no-op update: only needed so RETURNING yields a row when another
        -- process inserted the same identity first (DO NOTHING returns nothing)
        do update set source_repo = excluded.source_repo
        returning id
        """,
        _snapshot_identity(scan),
    ).fetchone()
    snapshot_id = int(row["id"])
    if scan.dependencies:
        db.executemany(
            """
            insert into snapshot_edges(
                snapshot_id, dependency_repo, dependency_repo_key, dependency_name,
                dependency_version, source_path, unresolved
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_id,
                    edge.dependency_repo,
                    repo_key(edge.dependency_repo) if edge.dependency_repo else None,
                    edge.dependency_name,
                    edge.dependency_version,
                    edge.source_path,
                    edge.unresolved,
                )
                for edge in scan.dependencies
            ],
        )
    return snapshot_id


def _touch_ref_scans(db: sqlite3.Connection, touches: tuple[RefScanTouch, ...]) -> None:
    if not touches:
        return
    db.executemany(
        """
        update source_ref_scans
        set checked_at = ?
        where source_key = ? and source_repo = ? and ref_kind = ? and source_ref = ?
        """,
        [
            (
                dump_dt(touch.checked_at),
                touch.source_key,
                touch.source_repo,
                touch.ref_kind,
                touch.source_ref,
            )
            for touch in touches
        ],
    )


def _upsert_source_repo_metadata(
    db: sqlite3.Connection,
    metadata: tuple[SourceRepoMetadata, ...],
) -> None:
    if not metadata:
        return
    db.executemany(
        """
        insert into source_repo_metadata(
            source_key, source_repo, source_repo_key, default_branch
        ) values (?, ?, ?, ?)
        on conflict(source_key, source_repo) do update set
            default_branch = excluded.default_branch
        """,
        [
            (row.source_key, row.source_repo, repo_key(row.source_repo), row.default_branch)
            for row in sorted(metadata, key=lambda item: (item.source_key, item.source_repo))
        ],
    )


def _prune_source_refs_for_repos(
    db: sqlite3.Connection,
    source_key: str,
    keep: set[tuple[str, str, str]],
    processed_repos: frozenset[str],
) -> None:
    if not processed_repos:
        return
    placeholders = ",".join("?" for _ in processed_repos)
    rows = db.execute(
        f"""
        select source_repo, ref_kind, source_ref
        from source_ref_scans
        where source_key = ? and source_repo in ({placeholders})
        """,
        (source_key, *sorted(processed_repos)),
    ).fetchall()
    stale: list[tuple[str, str, str]] = []
    for row in rows:
        key = (str(row["source_repo"]), str(row["ref_kind"]), str(row["source_ref"]))
        if key not in keep:
            stale.append(key)
    if stale:
        db.executemany(
            """
            delete from source_ref_scans
            where source_key = ? and source_repo = ? and ref_kind = ? and source_ref = ?
            """,
            [(source_key, repo, ref_kind, source_ref) for repo, ref_kind, source_ref in stale],
        )


def _prune_source_refs_to_repos(
    db: sqlite3.Connection,
    source_key: str,
    source_repos: frozenset[str],
) -> None:
    if not source_repos:
        db.execute("delete from source_ref_scans where source_key = ?", (source_key,))
        return
    placeholders = ",".join("?" for _ in source_repos)
    db.execute(
        f"""
        delete from source_ref_scans
        where source_key = ? and source_repo not in ({placeholders})
        """,
        (source_key, *sorted(source_repos)),
    )


def _prune_source_repo_metadata_to_repos(
    db: sqlite3.Connection,
    source_key: str,
    source_repos: frozenset[str],
) -> None:
    if not source_repos:
        db.execute("delete from source_repo_metadata where source_key = ?", (source_key,))
        return
    placeholders = ",".join("?" for _ in source_repos)
    db.execute(
        f"""
        delete from source_repo_metadata
        where source_key = ? and source_repo not in ({placeholders})
        """,
        (source_key, *sorted(source_repos)),
    )


def _refresh_source_run_from_ref_scans(
    db: sqlite3.Connection,
    source_key: str,
    *,
    scanned_at: datetime,
) -> None:
    row = db.execute(
        """
        select
            count(distinct source_repo) as repos,
            count(*) as refs
        from source_ref_scans
        where source_key = ?
        """,
        (source_key,),
    ).fetchone()
    # A completed refresh keeps its row even with no refs: the source exists, empty.
    refs = int(row["refs"] or 0)
    edge_row = db.execute(
        """
        select count(*) as edges
        from source_ref_scans as scans
        join snapshot_edges as edges
          on edges.snapshot_id = scans.snapshot_id
        where scans.source_key = ?
        """,
        (source_key,),
    ).fetchone()
    db.execute(
        """
        insert into source_runs(source_key, scanned_at, repos, refs, edges)
        values (?, ?, ?, ?, ?)
        on conflict(source_key) do update set
            scanned_at = excluded.scanned_at,
            repos = excluded.repos,
            refs = excluded.refs,
            edges = excluded.edges
        """,
        (
            source_key,
            dump_dt(scanned_at),
            int(row["repos"] or 0),
            refs,
            int(edge_row["edges"] or 0),
        ),
    )


def _mark_refresh_progress(
    db: sqlite3.Connection,
    source_key: str,
    source_fingerprint: str,
    statuses: dict[str, str],
) -> None:
    updated_at = dump_dt(datetime.now(UTC))
    db.executemany(
        """
        insert into source_refresh_progress(
            source_key, source_fingerprint, source_repo, status, updated_at
        ) values (?, ?, ?, ?, ?)
        on conflict(source_key, source_fingerprint, source_repo) do update set
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
        [
            (source_key, source_fingerprint, repo, status, updated_at)
            for repo, status in sorted(statuses.items())
        ],
    )


_HOWS: tuple[How, ...] = ("defines", "sets", "reads")
"""How each ``variable_facts.how`` integer reads: its index in this tuple."""


def _record_analysis(
    db: sqlite3.Connection,
    analysed: tuple[AnalysedFile, ...],
    listings: tuple[CommitListing, ...],
    *,
    analyser: str,
) -> None:
    """Store ``analysed`` files (replacing older facts of each) and mark ``listings`` analysed."""
    ids: dict[tuple[str, str], int] = {}
    for item in analysed:
        row = db.execute(
            """
            insert into variable_files(blob, path, analyser, skipped) values (?, ?, ?, ?)
            on conflict(blob, path) do update set
                analyser = excluded.analyser, skipped = excluded.skipped
            returning id
            """,
            (item.blob, item.path, analyser, item.analysis.skipped),
        ).fetchone()
        file_id = int(row["id"])
        ids[(item.blob, item.path)] = file_id
        db.execute("delete from variable_facts where file_id = ?", (file_id,))
        db.executemany(
            """
            insert into variable_facts(file_id, name, how, line, key_path)
            values (?, ?, ?, ?, ?)
            """,
            [
                (file_id, fact.name, _HOWS.index(fact.how), fact.line, json.dumps(fact.key_path))
                for fact in item.analysis.facts
            ],
        )
    known = {(blob, path) for listing in listings for path, blob in listing.files} - set(ids)
    for chunk in batched(sorted(known), 400, strict=False):
        values = ",".join("(?, ?)" for _ in chunk)
        for row in db.execute(
            f"""
            with requested(blob, path) as (values {values})
            select files.id, files.blob, files.path from variable_files as files
            join requested on requested.blob = files.blob and requested.path = files.path
            """,
            [value for pair in chunk for value in pair],
        ):
            ids[(str(row["blob"]), str(row["path"]))] = int(row["id"])
    for listing in listings:
        key = repo_key(listing.repo)
        missing = [path for path, blob in listing.files if (blob, path) not in ids]
        if missing:
            raise DependencyIndexError(
                f"{listing.repo}@{listing.sha}: {missing[0]} was never analysed; "
                "another refresh removed it, run the refresh again"
            )
        db.execute(
            "delete from variable_listing where source_repo_key = ? and source_sha = ?",
            (key, listing.sha),
        )
        db.executemany(
            """
            insert or ignore into variable_listing(source_repo_key, source_sha, file_id)
            values (?, ?, ?)
            """,
            [(key, listing.sha, ids[(blob, path)]) for path, blob in listing.files],
        )
        db.execute(
            """
            insert into variable_commits(source_repo_key, source_sha, analyser) values (?, ?, ?)
            on conflict(source_repo_key, source_sha) do update set analyser = excluded.analyser
            """,
            (key, listing.sha, analyser),
        )


def _delete_orphan_variable_facts(db: sqlite3.Connection) -> None:
    """Drop the analysis of commits no ref scan points at, then files no commit lists.

    Foreign keys are off (an older writer replaces rows it knows), so facts
    are deleted by file id rather than by cascade.
    """
    # Commits are few (one per indexed ref); listings, many, go by their key.
    commits = db.execute(
        """
        select source_repo_key, source_sha from variable_commits as commits
        where not exists (
            select 1 from source_ref_scans as scans
            where scans.source_repo_key = commits.source_repo_key
              and scans.source_sha = commits.source_sha
        )
        """
    ).fetchall()
    for statement in (
        "delete from variable_listing where source_repo_key = ? and source_sha = ?",
        "delete from variable_commits where source_repo_key = ? and source_sha = ?",
    ):
        db.executemany(statement, [(row[0], row[1]) for row in commits])
    orphans = [
        int(row[0])
        for row in db.execute(
            """
            select id from variable_files
            where not exists (select 1 from variable_listing where file_id = variable_files.id)
            """
        )
    ]
    for chunk in batched(orphans, 500, strict=False):
        marks = ",".join("?" for _ in chunk)
        db.execute(f"delete from variable_facts where file_id in ({marks})", chunk)
        db.execute(f"delete from variable_files where id in ({marks})", chunk)


def _delete_orphan_snapshots(db: sqlite3.Connection) -> None:
    db.execute(
        """
        delete from snapshot_edges
        where snapshot_id not in (
            select snapshot_id from source_ref_scans
        )
        """
    )
    db.execute(
        """
        delete from dependency_snapshots
        where id not in (
            select snapshot_id from source_ref_scans
        )
        """
    )
    _delete_orphan_variable_facts(db)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
