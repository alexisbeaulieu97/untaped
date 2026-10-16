"""SQLite schema and schema-version helpers for the dependency index."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from untaped_ansible.errors import DependencyIndexError

# Version 4 added lowercase ``*_repo_key`` columns: GitHub repo ids are
# case-insensitive, and repo joins compare these keys with BINARY collation so
# they can use the indexes (``collate nocase`` joins forced full scans). The
# ``*_repo`` columns keep the display casing. Version 5 added the four
# ``variable_*`` tables (variable facts per analysed file, which files each
# commit has, and which commits were analysed), only adding to version 4.
SCHEMA_VERSION = 5
#: The oldest schema version this one only adds to (new tables or columns,
#: never a change to an existing one). Stamped in ``schema_meta`` beside
#: ``user_version``: a reader whose ``SCHEMA_VERSION`` is at least a newer
#: index's ``additive_since`` opens it and reads its own tables by name, so
#: 11.x releases sharing one ``~/.untaped`` share the index. A change to an
#: existing table raises it to the new ``SCHEMA_VERSION``. Foreign keys are
#: off and an older writer deletes and replaces rows in the tables it knows,
#: so a table a newer version adds must tolerate rows whose parent is gone.
ADDITIVE_SINCE = 4

SCHEMA_SQL = """
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
create index if not exists idx_source_ref_scans_commit
    on source_ref_scans(source_repo_key, source_sha);
create index if not exists idx_source_repo_metadata_source
    on source_repo_metadata(source_key, source_repo_key);
create index if not exists idx_source_refresh_progress_source
    on source_refresh_progress(source_key, source_fingerprint, source_repo);

create table if not exists variable_files (
    id integer primary key,
    blob text not null,
    path text not null,
    analyser text not null,
    skipped text,
    unique(blob, path)
);

create table if not exists variable_facts (
    file_id integer not null references variable_files(id),
    name text not null,
    how integer not null,
    line integer not null,
    key_path text not null
);

create table if not exists variable_listing (
    source_repo_key text not null,
    source_sha text not null,
    file_id integer not null references variable_files(id),
    primary key (source_repo_key, source_sha, file_id)
);

create table if not exists variable_commits (
    source_repo_key text not null,
    source_sha text not null,
    analyser text not null,
    primary key (source_repo_key, source_sha)
);

create index if not exists idx_variable_facts_name
    on variable_facts(name, file_id);
create index if not exists idx_variable_facts_file
    on variable_facts(file_id);
create index if not exists idx_variable_listing_file
    on variable_listing(file_id);
"""


def ensure_schema(db: sqlite3.Connection, path: Path, *, warn: Callable[[str], None]) -> None:
    """Create dependency index tables on a fresh database.

    Databases stamped with the current ``SCHEMA_VERSION`` pass through
    (one from before ``schema_meta`` gets its stamp). An older one this
    version only adds to (its version is at least ``ADDITIVE_SINCE``) gets
    the new tables in place, keeping its rows; any other older (or
    unstamped) database is a stale cache: its tables are dropped and
    recreated empty, with a ``warn`` to refresh saved sources. A database
    written by a newer untaped release opens when its ``additive_since`` is
    at most this release's ``SCHEMA_VERSION`` (it only added tables or
    columns this release never reads); otherwise it is rejected, so a
    downgrade never destroys data a newer install still reads.
    """
    version = int(db.execute("pragma user_version").fetchone()[0])
    if version == SCHEMA_VERSION:
        if _additive_since(db) is None:
            _stamp(db)
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
    if ADDITIVE_SINCE <= version < SCHEMA_VERSION:
        # Every statement is ``if not exists``, so the bootstrap itself is the
        # upgrade; two processes running it at once both end at this version.
        _bootstrap(db)
        return
    if version != 0 or _has_tables(db):
        _drop_tables(db)
        warn(
            f"rebuilt the outdated dependency index at {path} (schema version "
            f"{version}, expected {SCHEMA_VERSION}); re-run "
            "'untaped ansible source refresh <name>' for each saved source",
        )
    _bootstrap(db)


def _bootstrap(db: sqlite3.Connection) -> None:
    """Create every missing table and stamp this version, atomically.

    Table creation and the version stamp must be one atomic unit: a crash
    between them would leave a version-0 database that already has tables,
    which :func:`ensure_schema` rejects as outdated. ``user_version`` is
    transactional in SQLite, so wrapping the script in begin/commit makes
    the whole bootstrap all-or-nothing.
    """
    db.executescript(
        f"begin;\n{SCHEMA_SQL}\n{_STAMP_SQL}\npragma user_version = {SCHEMA_VERSION};\ncommit;"
    )


_STAMP_SQL = f"""
create table if not exists schema_meta (
    id integer primary key check (id = 1),
    additive_since integer not null
);
insert or replace into schema_meta (id, additive_since) values (1, {ADDITIVE_SINCE});
"""


def _stamp(db: sqlite3.Connection) -> None:
    """Stamp an index from before ``schema_meta``; a read-only one is read unstamped."""
    try:
        db.executescript(f"begin;\n{_STAMP_SQL}\ncommit;")
    except sqlite3.OperationalError as exc:
        if "readonly" not in str(exc):
            raise
        db.rollback()


def _additive_since(db: sqlite3.Connection) -> int | None:
    """The index's ``schema_meta.additive_since``; ``None`` when it has no stamp."""
    try:
        row = db.execute("select additive_since from schema_meta where id = 1").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise  # locked or unreadable: not the same as written before the stamp
        return None
    return None if row is None else int(row[0])


def holds_no_source(path: Path) -> bool:
    """Whether the index at ``path`` has no source refreshed into it (read-only, never raises).

    What ``setup migrate-dirs`` replaces with the 10.x index: a command that
    only opened the new one created its tables and nothing else.
    """
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            return not any(
                db.execute(f"select 1 from {table} limit 1").fetchone()
                for table in ("source_runs", "source_ref_scans", "source_refresh_progress")
            )
    except sqlite3.Error, OSError, ValueError:
        return False


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
