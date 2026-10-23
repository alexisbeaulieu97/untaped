"""``assert_schema_pinned``: a DDL change needs its version bump, and history stays as released."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from untaped.sdk.testing import assert_schema_pinned

V1 = """
create table items (
    id integer primary key,
    name text not null,
    unique(name)
);
create index idx_items_name on items(name);
"""


def _pin(pins: Path, version: int, ddl: str, *, additive_since: int) -> None:
    pins.mkdir(exist_ok=True)
    (pins / f"v{version}.sql").write_text(ddl, encoding="utf-8")
    path = pins / "pins.json"
    recorded = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    recorded[str(version)] = {
        "sha256": hashlib.sha256(ddl.encode()).hexdigest(),
        "additive_since": additive_since,
    }
    path.write_text(json.dumps(recorded), encoding="utf-8")


@pytest.fixture
def pins(tmp_path: Path) -> Path:
    folder = tmp_path / "pins"
    _pin(folder, 1, V1, additive_since=1)
    return folder


def test_the_pinned_ddl_passes(pins: Path) -> None:
    assert_schema_pinned(V1, version=1, additive_since=1, pins=pins)


def test_a_changed_ddl_under_the_same_version_fails(pins: Path) -> None:
    with pytest.raises(AssertionError, match="not pinned as schema 1: if it changed, bump"):
        assert_schema_pinned(
            V1 + "\ncreate table more (id integer);", version=1, additive_since=1, pins=pins
        )


def test_a_new_version_not_yet_saved_fails(pins: Path) -> None:
    with pytest.raises(AssertionError, match="not pinned as schema 2"):
        assert_schema_pinned(V1, version=2, additive_since=1, pins=pins)


def test_an_edited_release_fails(pins: Path) -> None:
    (pins / "v1.sql").write_text(V1 + "-- edited", encoding="utf-8")
    with pytest.raises(AssertionError, match="no longer matches its pin"):
        assert_schema_pinned(V1, version=1, additive_since=1, pins=pins)


def test_a_missing_release_fails(pins: Path) -> None:
    (pins / "v1.sql").unlink()
    with pytest.raises(AssertionError, match=r"records schema 1 but .* is missing"):
        assert_schema_pinned(V1, version=1, additive_since=1, pins=pins)


def test_additive_since_changes_only_with_a_new_version(pins: Path) -> None:
    with pytest.raises(AssertionError, match="additive_since changes only with a new schema"):
        assert_schema_pinned(V1, version=1, additive_since=2, pins=pins)


ADDED = (
    V1.replace(
        "    name text not null,\n",
        "    name text not null,\n    note text,\n    seen integer not null default 0,\n",
    )
    + """
create table extra (id integer primary key);
create index idx_items_note on items(note);
"""
)


def test_new_tables_and_nullable_or_defaulted_columns_are_additive(pins: Path) -> None:
    _pin(pins, 2, ADDED, additive_since=1)
    assert_schema_pinned(ADDED, version=2, additive_since=1, pins=pins)


@pytest.mark.parametrize(
    ("ddl", "problem"),
    [
        (V1.replace("name text not null", "name integer not null"), "column items.name changed"),
        (V1.replace("unique(name)", "unique(id, name)"), "a constraint of table items changed"),
        ("create table other (id integer);", "table items is gone"),
        (V1.replace("name text not null,", "name text not null, must text not null,"),
         "new column items.must is not null with no default"),
    ],
)  # fmt: skip
def test_a_change_to_an_existing_table_is_not_additive(pins: Path, ddl: str, problem: str) -> None:
    _pin(pins, 2, ddl, additive_since=1)
    with pytest.raises(AssertionError, match=problem):
        assert_schema_pinned(ddl, version=2, additive_since=1, pins=pins)


def test_a_break_is_fine_once_additive_since_moves_past_it(pins: Path) -> None:
    ddl = "create table other (id integer);"
    _pin(pins, 2, ddl, additive_since=2)
    assert_schema_pinned(ddl, version=2, additive_since=2, pins=pins)
