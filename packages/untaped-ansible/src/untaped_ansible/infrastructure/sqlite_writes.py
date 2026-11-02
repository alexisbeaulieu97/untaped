"""The index's writes: one statement group per change, run inside the index's transaction.

:class:`~untaped_ansible.infrastructure.sqlite_index.SqliteDependencyIndex`
calls these with its open connection; each replaces, touches or prunes the
rows of one kind (ref scans and their snapshots, source repo metadata, a
refresh run's progress, analysed variable facts) and never commits.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from itertools import batched

from untaped_ansible.domain.identity import repo_key
from untaped_ansible.domain.payloads import RefScan, RefScanTouch, SourceRepoMetadata
from untaped_ansible.domain.variable_models import AnalysedFile, CommitListing
from untaped_ansible.errors import DependencyIndexError
from untaped_ansible.infrastructure.sqlite_rows import HOWS, dump_dt

_SnapshotIdentity = tuple[str, str, str, str]


def _snapshot_identity(scan: RefScan) -> _SnapshotIdentity:
    return (
        scan.source_repo,
        scan.source_sha,
        scan.dependency_paths_fingerprint,
        scan.aliases_fingerprint,
    )


def replace_ref_scans(db: sqlite3.Connection, scans: tuple[RefScan, ...]) -> None:
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


def touch_ref_scans(db: sqlite3.Connection, touches: tuple[RefScanTouch, ...]) -> None:
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


def upsert_source_repo_metadata(
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


def prune_source_refs_for_repos(
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


def prune_source_refs_to_repos(
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


def prune_source_repo_metadata_to_repos(
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


def refresh_source_run_from_ref_scans(
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


def mark_refresh_progress(
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


def record_analysis(
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
                (file_id, fact.name, HOWS.index(fact.how), fact.line, json.dumps(fact.key_path))
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


def delete_orphan_snapshots(db: sqlite3.Connection) -> None:
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
