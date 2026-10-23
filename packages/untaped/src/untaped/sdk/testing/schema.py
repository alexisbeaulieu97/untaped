"""``assert_schema_pinned``: a SQLite store's DDL changes only with the right version bump.

A store that two releases may share keeps its schema history beside its
tests: one ``v<N>.sql`` per released schema version (the DDL as released)
and a ``pins.json`` of ``{"<N>": {"sha256": …, "additive_since": M}}``,
appended, never edited. The check reads both and today's DDL, and fails
when the DDL changed under the same version, when the history was edited,
or when a version today's code claims to only add to is not additive.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from untaped.sdk.marks import experimental

_PINS = "pins.json"

#: One column as ``pragma table_xinfo`` describes it: name, type, not null, default, key.
type _Column = tuple[str, str, int, str | None, int]


@experimental
def assert_schema_pinned(ddl: str, *, version: int, additive_since: int, pins: Path) -> None:
    """Fail unless ``ddl`` is pinned as ``version`` and additive over every version it claims.

    ``ddl`` is today's schema script, ``version`` its schema version and
    ``additive_since`` the oldest version it only adds to (new tables, new
    columns with a default or nullable). ``pins`` is the directory holding
    ``v<N>.sql`` per released version and ``pins.json``. Raises
    ``AssertionError`` naming what to do: bump the version for a changed
    DDL, restore an edited history, or bump ``additive_since`` for a change
    to an existing table. Indexes are not compared.
    """
    recorded = _read_pins(pins)
    for pinned, pin in sorted(recorded.items()):
        path = pins / f"v{pinned}.sql"
        if not path.is_file():
            raise AssertionError(f"{_PINS} records schema {pinned} but {path} is missing")
        if _sha256(path.read_text(encoding="utf-8")) != pin.get("sha256"):
            raise AssertionError(
                f"{path} no longer matches its pin in {_PINS}: released schemas are never edited; "
                "restore it, and give a changed DDL a new version"
            )
    current = recorded.get(version)
    if current is None or current.get("sha256") != _sha256(ddl):
        raise AssertionError(
            f"the DDL is not pinned as schema {version}: if it changed, bump the schema version; "
            f"bump additive_since too if any existing table changed; then save the DDL as "
            f"v<N>.sql in {pins} and append its pin to {_PINS}"
        )
    if current.get("additive_since") != additive_since:
        raise AssertionError(
            f"schema {version} is pinned with additive_since {current.get('additive_since')}, "
            f"not {additive_since}: additive_since changes only with a new schema version"
        )
    today = _tables(ddl)
    for pinned in sorted(recorded):
        if additive_since <= pinned < version:
            older = (pins / f"v{pinned}.sql").read_text(encoding="utf-8")
            problem = _not_additive(_tables(older), today)
            if problem is not None:
                raise AssertionError(
                    f"schema {version} claims to only add to schema {pinned} "
                    f"(additive_since {additive_since}), but {problem}: raise additive_since "
                    f"to {version}"
                )


def _read_pins(pins: Path) -> dict[int, dict[str, Any]]:
    path = pins / _PINS
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AssertionError(f"could not read {path}: {exc}") from None
    if not isinstance(raw, dict):
        raise AssertionError(f"{path} must map each schema version to its pin")
    return {int(key): value for key, value in raw.items()}


def _sha256(text: str) -> str:
    """The sha256 of ``text`` (read in text mode: line endings are already ``\\n``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tables(ddl: str) -> dict[str, tuple[list[_Column], set[tuple[Any, ...]]]]:
    """Each table's columns and constraints, as SQLite reads ``ddl``."""
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(ddl)
        names = [
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
            )
        ]
        found: dict[str, tuple[list[_Column], set[tuple[Any, ...]]]] = {}
        for name in names:
            columns = [
                (str(row[1]), str(row[2]), int(row[3]), row[4], int(row[5]))
                for row in db.execute(f'pragma table_xinfo("{name}")')
            ]
            constraints: set[tuple[Any, ...]] = set()
            for index in db.execute(f'pragma index_list("{name}")'):
                if index[3] in ("u", "pk"):  # a unique or primary key constraint, not an index
                    keyed = tuple(row[2] for row in db.execute(f'pragma index_info("{index[1]}")'))
                    constraints.add(("unique", index[3], keyed))
            for row in db.execute(f'pragma foreign_key_list("{name}")'):
                constraints.add(("references", row[2], row[3], row[4], row[5], row[6]))
            found[name] = (columns, constraints)
        return found


def _not_additive(
    older: dict[str, tuple[list[_Column], set[tuple[Any, ...]]]],
    today: dict[str, tuple[list[_Column], set[tuple[Any, ...]]]],
) -> str | None:
    """What makes ``today`` more than additions to ``older``, or ``None``."""
    for table, (columns, constraints) in older.items():
        if table not in today:
            return f"table {table} is gone"
        now_columns, now_constraints = today[table]
        by_name = {column[0]: column for column in now_columns}
        for column in columns:
            if by_name.get(column[0]) != column:
                return f"column {table}.{column[0]} changed"
        missing = constraints - now_constraints
        if missing:
            return f"a constraint of table {table} changed"
        if now_constraints - constraints:
            return f"table {table} has a new constraint an older writer does not keep"
        kept = {column[0] for column in columns}
        for name, _type, notnull, default, _key in now_columns:
            if name not in kept and notnull and default is None:
                return f"new column {table}.{name} is not null with no default"
    return None


__all__ = ["assert_schema_pinned"]
