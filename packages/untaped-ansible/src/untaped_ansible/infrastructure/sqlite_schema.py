"""SQLite schema and schema-version helpers for the dependency index."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from untaped.sdk import Diagnostics
from untaped_ansible.errors import DependencyIndexError

# Version 4 added lowercase ``*_repo_key`` columns: GitHub repo ids are
# case-insensitive, and repo joins compare these keys with BINARY collation so
# they can use the indexes (``collate nocase`` joins forced full scans). The
# ``*_repo`` columns keep the display casing.
SCHEMA_VERSION = 4
#: The oldest schema version this one only adds to (new tables or columns,
#: never a change to an existing one). Stamped in ``schema_meta`` beside
#: ``user_version``: a reader whose ``SCHEMA_VERSION`` is at least a newer
#: index's ``additive_since`` opens it and reads its own tables by name, so
#: 11.x releases sharing one ``~/.untaped`` share the index. A change to an
#: existing table raises it to the new ``SCHEMA_VERSION``.
ADDITIVE_SINCE = 4

_diag = Diagnostics()

_SCHEMA_SQL = """
create table if not exists source_runs (
    source_key text primary key,
    scanned_at text not null,
    repos integer not null default 0,
    refs integer not null default 0,
    edges integer not null default 0
);

create table if not exists dependency_snapshots (
    id integer primary key autoincrement,
    source_repo text not null,
    source_sha text,
    dependency_paths_fingerprint text not null,
    aliases_fingerprint text not null default '',
    unique(source_repo, source_sha, dependency_paths_fingerprint, aliases_fingerprint)
);

create table if not exists snapshot_edges (
    id integer primary key autoincrement,
    snapshot_id integer not null references dependency_snapshots(id) on delete cascade,
    dependency_repo text,
    dependency_repo_key text,
    dependency_name text not null,
    dependency_version text,
    source_path text not null,
    unresolved text
);

create table if not exists source_ref_scans (
    id integer primary key autoincrement,
    source_key text not null,
    source_repo text not null,
    source_repo_key text not null,
    ref_kind text not null,
    source_ref text not null,
    source_sha text not null,
    clone_url text,
    clone_protocol text,
    dependency_paths_fingerprint text not null,
    aliases_fingerprint text not null default '',
    checked_at text not null,
    indexed_at text not null,
    snapshot_id integer not null references dependency_snapshots(id),
    unique(source_key, source_repo, ref_kind, source_ref)
);

create table if not exists source_repo_metadata (
    source_key text not null,
    source_repo text not null,
    source_repo_key text not null,
    default_branch text not null,
    primary key (source_key, source_repo)
);

create table if not exists source_refresh_progress (
    source_key text not null,
    source_fingerprint text not null,
    source_repo text not null,
    status text not null,
    updated_at text not null,
    primary key (source_key, source_fingerprint, source_repo)
);

create index if not exists idx_dependency_snapshots_identity
    on dependency_snapshots(
        source_repo, source_sha, dependency_paths_fingerprint, aliases_fingerprint
    );
create index if not exists idx_snapshot_edges_dependency
    on snapshot_edges(snapshot_id, dependency_repo, dependency_version);
create index if not exists idx_snapshot_edges_dependency_ref
    on snapshot_edges(dependency_repo_key, dependency_version, snapshot_id);
create index if not exists idx_source_ref_scans_source
    on source_ref_scans(source_key, source_repo, ref_kind, source_ref);
create index if not exists idx_source_ref_scans_source_ref
    on source_ref_scans(source_key, source_repo_key, source_ref);
create index if not exists idx_source_ref_scans_snapshot
    on source_ref_scans(snapshot_id);
create index if not exists idx_source_ref_scans_source_snapshot
    on source_ref_scans(source_key, snapshot_id);
create index if not exists idx_source_repo_metadata_source
    on source_repo_metadata(source_key, source_repo_key);
create index if not exists idx_source_refresh_progress_source
    on source_refresh_progress(source_key, source_fingerprint, source_repo);
"""


def ensure_schema(db: sqlite3.Connection, path: Path) -> None:
    """Create dependency index tables on a fresh database.

    Databases stamped with the current ``SCHEMA_VERSION`` pass through
    (one from before ``schema_meta`` gets its stamp). An older (or
    unstamped) database is a stale cache: its tables are dropped and
    recreated empty, with a warning to refresh saved sources. A database
    written by a newer untaped release opens when its ``additive_since`` is
    at most this release's ``SCHEMA_VERSION`` (it only added tables or
    columns this release never reads); otherwise it is rejected, so a
    downgrade never destroys data a newer install still reads.
    """
    version = int(db.execute("pragma user_version").fetchone()[0])
    if version == SCHEMA_VERSION:
        if _additive_since(db) is None:
            db.executescript(f"begin;\n{_STAMP_SQL}\ncommit;")
        return
    if version > SCHEMA_VERSION:
        additive_since = _additive_since(db)
        if additive_since is not None and additive_since <= SCHEMA_VERSION:
            return
        raise DependencyIndexError(
            f"index schema version {version} was written by a newer untaped release "
            f"(this release reads version {SCHEMA_VERSION}); upgrade untaped, or delete "
            f"{path} and re-run 'untaped ansible source refresh <name>'",
            category="config",
        )
    if version != 0 or _has_tables(db):
        _drop_tables(db)
        _diag.warning(
            f"rebuilt the outdated dependency index at {path} (schema version "
            f"{version}, expected {SCHEMA_VERSION}); re-run "
            "'untaped ansible source refresh <name>' for each saved source",
        )
    # Table creation and the version stamp must be one atomic unit: a crash
    # between them would leave a version-0 database that already has tables,
    # which the check above rejects as outdated. ``user_version`` is
    # transactional in SQLite, so wrapping the script in begin/commit makes
    # the whole bootstrap all-or-nothing.
    db.executescript(
        f"begin;\n{_SCHEMA_SQL}\n{_STAMP_SQL}\npragma user_version = {SCHEMA_VERSION};\ncommit;"
    )


_STAMP_SQL = f"""
create table if not exists schema_meta (
    id integer primary key check (id = 1),
    additive_since integer not null
);
insert or replace into schema_meta (id, additive_since) values (1, {ADDITIVE_SINCE});
"""


def _additive_since(db: sqlite3.Connection) -> int | None:
    """The index's ``schema_meta.additive_since``; ``None`` when it has no stamp."""
    try:
        row = db.execute("select additive_since from schema_meta where id = 1").fetchone()
    except sqlite3.OperationalError:  # no schema_meta table: written before the stamp
        return None
    return None if row is None else int(row[0])


def _drop_tables(db: sqlite3.Connection) -> None:
    names = [
        str(row[0])
        for row in db.execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
        )
    ]
    statements = "".join(f'drop table if exists "{name}";\n' for name in names)
    db.executescript(f"begin;\n{statements}pragma user_version = 0;\ncommit;")


def _has_tables(db: sqlite3.Connection) -> bool:
    row = db.execute("select count(*) from sqlite_master where type = 'table'").fetchone()
    return int(row[0]) > 0
