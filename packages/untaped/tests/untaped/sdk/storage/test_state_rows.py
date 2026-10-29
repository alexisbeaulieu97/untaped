"""StateRows: typed rows a plugin class keeps, a project's rows, and the unknown-fields rule."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Command, Key, Plugin
from untaped.sdk.errors import ConfigError, UntapedError
from untaped.sdk.fields import Positional
from untaped.sdk.storage.state import ProjectStateRows, RowExists, StateRows
from untaped.sdk.testing import install, make_project, read_rows, write_rows


class Repo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, Key]
    url: str
    branch: str | None = None


class Owner(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    team: str
    email: str = ""


class Item(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, Key]
    created_at: str
    owner: Owner | None = None
    repos: tuple[Repo, ...] = ()
    labels: dict[str, str] = Field(default_factory=dict)


ITEMS = StateRows[Item]()
SPARSE = StateRows[Repo](omit_none=True)
PER_PROJECT = StateRows[Item].per_project()


class Rm(Command):
    """Remove an item."""

    removes = ITEMS

    name: Positional[str] = Field(description="The item's name.")

    def run(self) -> None:
        return None


class Settings(BaseModel):
    pass


class Demo(Plugin):
    """Demo."""

    settings = Settings
    commands = (Rm,)
    items = ITEMS
    sparse = SPARSE
    checkouts = PER_PROJECT


def _item(name: str, **changes: object) -> Item:
    return Item.model_validate({"name": name, "created_at": "2026-10-02T09:12:00Z", **changes})


def _state_text() -> str:
    return ITEMS.path().read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _demo(_isolated_config: Path) -> Iterator[None]:
    with install(Demo).running():
        yield


# -- reads and writes -------------------------------------------------------


def test_add_get_all_keys() -> None:
    ITEMS.add(_item("a"))
    ITEMS.add(_item("b"))

    assert ITEMS.get("a") == _item("a")
    assert ITEMS.get("zzz") is None
    assert ITEMS.all() == [_item("a"), _item("b")]
    assert ITEMS.keys() == ["a", "b"]


def test_add_refuses_a_taken_key() -> None:
    ITEMS.add(_item("a"))

    with pytest.raises(RowExists) as raised:
        ITEMS.add(_item("a", created_at="later"))

    assert (raised.value.exit_code, raised.value.category) == (1, "conflict")
    assert raised.value.details == {"path": str(ITEMS.path()), "key": "a"}
    assert ITEMS.all() == [_item("a")]


def test_put_replaces_in_place_and_appends_a_new_key() -> None:
    ITEMS.add(_item("a"))
    ITEMS.add(_item("b"))

    ITEMS.put(_item("a", created_at="later"))
    ITEMS.put(_item("c"))

    assert ITEMS.keys() == ["a", "b", "c"]
    assert ITEMS.get("a") == _item("a", created_at="later")


def test_update_changes_one_row_and_reports_a_missing_one() -> None:
    ITEMS.add(_item("a"))

    updated = ITEMS.update("a", lambda row: row.model_copy(update={"created_at": "later"}))

    assert updated == _item("a", created_at="later")
    assert ITEMS.get("a") == updated
    assert ITEMS.update("zzz", lambda row: row) is None


def test_update_refuses_a_changed_key() -> None:
    ITEMS.add(_item("a"))

    with pytest.raises(UntapedError, match="changed the row's key"):
        ITEMS.update("a", lambda row: row.model_copy(update={"name": "b"}))

    assert ITEMS.keys() == ["a"]


def test_replace_all_runs_on_every_row_and_refuses_a_repeated_key() -> None:
    ITEMS.add(_item("a"))

    replaced = ITEMS.replace_all(lambda rows: [*rows, _item("b")])

    assert replaced == [_item("a"), _item("b")]
    assert ITEMS.keys() == ["a", "b"]
    with pytest.raises(UntapedError, match="key 'a' twice"):
        ITEMS.replace_all(lambda rows: [*rows, _item("a")])
    assert ITEMS.keys() == ["a", "b"]


def test_delete_removes_every_copy_and_drops_the_key() -> None:
    write_rows(ITEMS, [{"name": "a", "created_at": "x"}, {"name": "a", "created_at": "y"}])

    assert ITEMS.delete("a") == 2
    assert ITEMS.delete("a") == 0
    assert "items" not in yaml.safe_load(_state_text())


def test_a_row_of_another_model_is_refused() -> None:
    with pytest.raises(UntapedError, match="keeps Item rows"):
        ITEMS.add(Repo(name="a", url="u"))  # type: ignore[arg-type]


def test_omit_none_leaves_none_fields_out() -> None:
    SPARSE.add(Repo(name="a", url="u"))

    assert read_rows(SPARSE) == [{"name": "a", "url": "u"}]


# -- damaged rows -----------------------------------------------------------


def test_a_damaged_row_stops_its_reads_with_the_file_row_and_fix() -> None:
    write_rows(ITEMS, [{"name": "a"}, {"name": "b", "created_at": "x"}])

    with pytest.raises(ConfigError) as raised:
        ITEMS.get("a")

    error = raised.value
    assert error.exit_code == 4
    assert str(error) == (
        f"demo.items row a in {ITEMS.path()} is damaged: created_at: Field required"
    )
    assert error.hint == "correct that row by hand, or remove it with `untaped demo rm a`"
    with pytest.raises(ConfigError, match="row a"):
        ITEMS.all()
    with pytest.raises(ConfigError, match="row a"):
        ITEMS.put(_item("a"))
    with pytest.raises(ConfigError, match="row a"):
        ITEMS.update("a", lambda row: row)
    # Other rows, keys and delete keep working.
    assert ITEMS.get("b") == _item("b", created_at="x")
    assert ITEMS.keys() == ["a", "b"]
    ITEMS.put(_item("c"))
    assert ITEMS.delete("a") == 1
    assert ITEMS.all() == [_item("b", created_at="x"), _item("c")]


def test_a_repeated_key_is_damaged() -> None:
    write_rows(ITEMS, [{"name": "a", "created_at": "x"}, {"name": "a", "created_at": "y"}])

    with pytest.raises(ConfigError, match=r"row a .* its key is repeated \(rows 1 and 2\)"):
        ITEMS.get("a")
    with pytest.raises(ConfigError, match="repeated"):
        ITEMS.all()
    assert ITEMS.keys() == ["a"]


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ({"created_at": "x"}, "it has no name"),
        ({"name": 3, "created_at": "x"}, "its name is not a string"),
        ("text", "it is not a mapping"),
    ],
)
def test_a_row_without_a_key_is_named_by_its_position(row: object, problem: str) -> None:
    write_rows(ITEMS, [{"name": "a", "created_at": "x"}, row])

    with pytest.raises(ConfigError, match=f"row #2 .* {problem}") as raised:
        ITEMS.all()

    assert raised.value.hint == "correct or remove that row by hand"
    assert ITEMS.get("a") == _item("a", created_at="x")
    assert ITEMS.keys() == ["a"]


# -- the unknown-fields rule -------------------------------------------------


def test_a_newer_untapeds_fields_are_read_and_written_back() -> None:
    write_rows(
        ITEMS,
        [
            {
                "name": "a",
                "created_at": "x",
                "pinned": True,
                "owner": {"team": "t", "pager": "p"},
                "repos": [{"name": "api", "url": "u", "sparse": ["docs"]}],
            }
        ],
    )

    assert ITEMS.get("a") == _item(
        "a", created_at="x", owner={"team": "t"}, repos=[{"name": "api", "url": "u"}]
    )

    def _append(row: Item) -> Item:
        owner = row.owner.model_copy(update={"email": "e"}) if row.owner else None
        repos = (*row.repos, Repo(name="web", url="w"))
        return row.model_copy(update={"repos": repos, "owner": owner})

    ITEMS.update("a", _append)

    assert read_rows(ITEMS) == [
        {
            "name": "a",
            "created_at": "x",
            "pinned": True,
            "owner": {"team": "t", "pager": "p", "email": "e"},
            "repos": [
                {"name": "api", "url": "u", "sparse": ["docs"]},
                {"name": "web", "url": "w", "branch": None},
            ],
            "labels": {},
        }
    ]


def test_a_dict_field_is_written_whole_so_deleted_keys_stay_deleted() -> None:
    write_rows(ITEMS, [{"name": "a", "created_at": "x", "labels": {"k": "v", "j": "w"}}])

    ITEMS.update("a", lambda row: row.model_copy(update={"labels": {"j": "w"}}))

    assert read_rows(ITEMS)[0]["labels"] == {"j": "w"}


def test_replace_all_keeps_a_stored_rows_unknown_fields() -> None:
    write_rows(ITEMS, [{"name": "a", "created_at": "x", "pinned": True}])

    ITEMS.replace_all(lambda rows: [*rows, _item("b")])

    assert read_rows(ITEMS)[0]["pinned"] is True


# -- a project's rows ----------------------------------------------------------


def test_a_project_row_keeps_unknown_fields_after_an_update(tmp_path: Path) -> None:
    rows = PER_PROJECT.of(make_project(tmp_path / "PROJ-1"))
    write_rows(rows, [{"name": "a", "created_at": "x", "pinned": True}])

    rows.update("a", lambda row: row.model_copy(update={"created_at": "y"}))

    assert read_rows(rows) == [
        {"name": "a", "created_at": "y", "pinned": True, "owner": None, "repos": [], "labels": {}}
    ]


def test_per_project_returns_a_declaration_with_of_only() -> None:
    assert isinstance(PER_PROJECT, ProjectStateRows)
    assert not hasattr(PER_PROJECT, "get")


# -- two processes ---------------------------------------------------------------

_WORKER = """
import os, sys
os.environ["UNTAPED_HOME"] = sys.argv[1]
sys.path.insert(0, sys.argv[3])
from untaped.sdk.testing import install
from test_state_rows import ITEMS, Demo, _item

with install(Demo).running():
    for n in range(10):
        name = f"{sys.argv[2]}{n}"
        ITEMS.add(_item(name))
        ITEMS.update(name, lambda row: row.model_copy(update={"created_at": "done"}))
"""


def test_two_processes_changing_rows_both_land(_isolated_config: Path) -> None:
    here = str(Path(__file__).parent)
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(_isolated_config.parent), prefix, here]
        )
        for prefix in ("p", "q")
    ]
    assert [worker.wait(60) for worker in workers] == [0, 0]

    rows = read_rows(ITEMS)
    assert len(rows) == 20
    assert all(row["created_at"] == "done" for row in rows)
