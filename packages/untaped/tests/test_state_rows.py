"""StateRows: typed rows in ``state.yml``, a project's rows, and the unknown-fields rule."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field

from untaped.errors import ConfigError, UntapedError
from untaped.settings import resolve_state_path
from untaped.state import (
    ProjectStateRows,
    RowExists,
    StateCollection,
    StateMap,
    StateRows,
    core_rows,
)
from untaped.testing import make_project, read_rows, write_rows


class Repo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    url: str
    branch: str | None = None


class Owner(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    team: str
    email: str = ""


class Item(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    created_at: str
    owner: Owner | None = None
    repos: tuple[Repo, ...] = ()
    labels: dict[str, str] = Field(default_factory=dict)


ITEMS = StateRows(
    "demo", "items", model=Item, key_field="name", remove_hint="untaped demo rm {key}"
)
PER_PROJECT = StateRows.per_project("demo", "checkouts", model=Item, key_field="name")


def _item(name: str, **changes: object) -> Item:
    return Item.model_validate({"name": name, "created_at": "2026-10-02T09:12:00Z", **changes})


def _state_text() -> str:
    return resolve_state_path().read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _home(_isolated_config: Path) -> None:
    pass


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

    assert raised.value.category == "conflict"
    assert raised.value.exit_code == 1
    assert ITEMS.all() == [_item("a")]


def test_put_replaces_in_place_and_appends_a_new_key() -> None:
    for name in ("a", "b", "c"):
        ITEMS.add(_item(name))

    ITEMS.put(_item("a", created_at="later"))
    ITEMS.put(_item("d"))

    assert ITEMS.keys() == ["a", "b", "c", "d"]
    assert ITEMS.get("a") == _item("a", created_at="later")


def test_update_changes_one_row_and_reports_a_missing_one() -> None:
    ITEMS.add(_item("a"))

    updated = ITEMS.update("a", lambda row: row.model_copy(update={"created_at": "later"}))

    assert updated == _item("a", created_at="later")
    assert ITEMS.get("a") == updated
    assert ITEMS.update("missing", lambda row: row) is None


def test_update_refuses_a_changed_key() -> None:
    ITEMS.add(_item("a"))

    with pytest.raises(UntapedError, match="changed the row's key"):
        ITEMS.update("a", lambda row: row.model_copy(update={"name": "b"}))

    assert ITEMS.keys() == ["a"]


def test_a_write_inside_update_is_refused_and_nothing_is_written() -> None:
    ITEMS.add(_item("a"))

    def _nested(row: Item) -> Item:
        ITEMS.put(_item("b"))
        return row.model_copy(update={"created_at": "later"})

    with pytest.raises(UntapedError, match="inside another write"):
        ITEMS.update("a", _nested)

    assert ITEMS.all() == [_item("a")]


def test_replace_all_runs_on_every_row_and_refuses_a_repeated_key() -> None:
    ITEMS.add(_item("a"))
    ITEMS.add(_item("b"))

    kept = ITEMS.replace_all(lambda rows: [row for row in rows if row.name != "a"] + [_item("c")])

    assert kept == [_item("b"), _item("c")]
    assert ITEMS.keys() == ["b", "c"]
    with pytest.raises(UntapedError, match="twice"):
        ITEMS.replace_all(lambda rows: [*rows, _item("b")])


def test_delete_removes_every_copy_and_collapses_the_section() -> None:
    write_rows(ITEMS, [{"name": "a", "created_at": "x"}, {"name": "a", "created_at": "y"}])

    assert ITEMS.delete("a") == 2
    assert ITEMS.delete("a") == 0
    assert "demo" not in (yaml.safe_load(_state_text()) or {})


def test_a_row_of_another_model_is_refused() -> None:
    with pytest.raises(UntapedError, match="keeps Item rows"):
        ITEMS.add(Repo(name="a", url="u"))  # type: ignore[arg-type]


def test_omit_none_leaves_none_fields_out() -> None:
    rows = StateRows("demo", "sparse", model=Repo, key_field="name", omit_none=True)

    rows.add(Repo(name="a", url="u"))

    assert read_rows(rows) == [{"name": "a", "url": "u"}]


def test_a_lazy_model_resolves_on_first_use() -> None:
    calls: list[int] = []

    def _model() -> type[Repo]:
        calls.append(1)
        return Repo

    rows = StateRows("demo", "lazy", model=_model, key_field="name")
    assert calls == []
    rows.add(Repo(name="a", url="u"))
    rows.get("a")
    assert calls == [1]


@pytest.mark.parametrize(
    ("key_field", "message"),
    [("missing", "must be a required str field"), ("branch", "must be a required str field")],
)
def test_the_key_field_is_a_required_str(key_field: str, message: str) -> None:
    rows = StateRows("demo", "bad", model=Repo, key_field=key_field)

    with pytest.raises(ConfigError, match=message):
        rows.keys()


def test_declaration_checks() -> None:
    with pytest.raises(ConfigError, match="plugin name"):
        StateRows("Not A Plugin", "x", model=Repo, key_field="name")
    with pytest.raises(ConfigError, match="one identifier"):
        StateRows("demo", "two.parts", model=Repo, key_field="name")
    with pytest.raises(ConfigError, match=r"\{key\}"):
        StateRows("demo", "x", model=Repo, key_field="name", remove_hint="untaped demo rm")


def test_a_full_disk_is_an_error_and_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    ITEMS.add(_item("a"))
    before = _state_text()

    def _full(*_: object, **__: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("untaped.config_file.atomic_write", _full)
    with pytest.raises(ConfigError, match="No space left on device; nothing changed"):
        ITEMS.add(_item("b"))

    assert _state_text() == before


# -- damaged rows -----------------------------------------------------------


def test_a_damaged_row_stops_its_reads_with_the_file_row_and_fix() -> None:
    write_rows(ITEMS, [{"name": "a"}, {"name": "b", "created_at": "x"}])

    with pytest.raises(ConfigError) as raised:
        ITEMS.get("a")

    error = raised.value
    assert error.exit_code == 4
    assert str(error) == (
        f"demo.items row a in {resolve_state_path()} is damaged: created_at: Field required"
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


# -- bytes on disk -----------------------------------------------------------

#: Rows in the layout v11.0.0's StateCollection wrote for ``model_dump(mode="json")`` rows.
GOLDEN = """\
format_version: 1
demo:
  items:
  - name: a
    created_at: '2026-10-02T09:12:00Z'
    owner: null
    repos:
    - name: api
      url: https://github.com/acme/api.git
      branch: main
    labels: {}
  - name: b
    created_at: '2026-10-02T09:12:00Z'
    owner: null
    repos: []
    labels: {}
"""


def test_rows_written_by_11_0_read_and_rewrite_byte_for_byte() -> None:
    path = resolve_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(GOLDEN, encoding="utf-8")

    assert ITEMS.get("a") == _item(
        "a", repos=[{"name": "api", "url": "https://github.com/acme/api.git", "branch": "main"}]
    )
    ITEMS.put(ITEMS.get("a"))  # type: ignore[arg-type]
    ITEMS.update("b", lambda row: row)
    ITEMS.replace_all(lambda rows: rows)
    assert _state_text() == GOLDEN

    # A changed row is written as 11.0's StateCollection would have written it.
    ITEMS.update("b", lambda row: row.model_copy(update={"created_at": "later"}))
    by_rows = _state_text()
    path.write_text(GOLDEN, encoding="utf-8")
    StateCollection("demo", "items").mutate(
        lambda rows: [row if row["name"] != "b" else {**row, "created_at": "later"} for row in rows]
    )
    assert by_rows == _state_text()


def test_a_new_row_dumps_as_11_0_did() -> None:
    value = _item("a", repos=[{"name": "api", "url": "u"}])
    StateCollection("demo", "items").upsert(value.model_dump(mode="json"))
    by_collection = _state_text()
    resolve_state_path().unlink()

    ITEMS.add(value)

    assert _state_text() == by_collection


# -- a project's rows ----------------------------------------------------------


def test_a_projects_rows_live_in_its_folder_next_to_the_homes(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ-1")
    ITEMS.add(_item("home"))
    rows = PER_PROJECT.of(project)

    rows.add(_item("here"))

    assert rows.path() == project.root / ".untaped" / "state.yml"
    assert rows.keys() == ["here"]
    assert ITEMS.keys() == ["home"]
    on_disk = yaml.safe_load(rows.path().read_text(encoding="utf-8"))
    assert on_disk["demo"]["checkouts"][0]["name"] == "here"
    assert "checkouts" not in yaml.safe_load(_state_text())["demo"]


def test_a_project_row_keeps_unknown_fields_after_an_update(tmp_path: Path) -> None:
    rows = PER_PROJECT.of(make_project(tmp_path / "PROJ-1"))
    write_rows(rows, [{"name": "a", "created_at": "x", "pinned": True}])

    rows.update("a", lambda row: row.model_copy(update={"created_at": "y"}))

    assert read_rows(rows) == [
        {"name": "a", "created_at": "y", "pinned": True, "owner": None, "repos": [], "labels": {}}
    ]


def test_a_project_string_map(tmp_path: Path) -> None:
    project = make_project(tmp_path / "PROJ-1")
    StateMap("demo", "aliases", project=project).set("x", "y")

    assert StateMap("demo", "aliases", project=project).entries() == {"x": "y"}
    assert StateMap("demo", "aliases").entries() == {}


def test_per_project_returns_a_declaration_with_of_only() -> None:
    assert isinstance(PER_PROJECT, ProjectStateRows)
    assert not hasattr(PER_PROJECT, "get")


# -- core's own rows -----------------------------------------------------------


def test_core_rows_live_in_a_section_core_reserves() -> None:
    rows = core_rows("credentials", "index", model=Repo, key_field="name")
    rows.add(Repo(name="a", url="u"))

    assert yaml.safe_load(_state_text())["credentials"]["index"][0]["name"] == "a"
    with pytest.raises(ConfigError, match="not a state section core reserves"):
        core_rows("demo", "index", model=Repo, key_field="name")
    with pytest.raises(ConfigError, match="section core reserves"):
        StateRows("credentials", "index", model=Repo, key_field="name")
    with pytest.raises(ConfigError, match="credentials"):
        StateMap("credentials", "index").set("a", "b")
    with pytest.raises(ConfigError, match="credentials"):
        StateCollection("credentials", "index").upsert({"id": "a"})
    assert rows.keys() == ["a"]


# -- two processes ---------------------------------------------------------------

_WORKER = """
import os, sys
os.environ["UNTAPED_CONFIG"] = sys.argv[1]
from pydantic import BaseModel
from untaped.state import StateRows

class Row(BaseModel):
    name: str
    done: bool = False

rows = StateRows("demo", "items", model=Row, key_field="name")
for n in range(10):
    rows.add(Row(name=f"{sys.argv[2]}{n}"))
    rows.update(f"{sys.argv[2]}{n}", lambda row: row.model_copy(update={"done": True}))
"""


def test_two_processes_changing_rows_both_land(_isolated_config: Path) -> None:
    workers = [
        subprocess.Popen([sys.executable, "-c", _WORKER, str(_isolated_config), prefix])
        for prefix in ("p", "q")
    ]
    assert [worker.wait(60) for worker in workers] == [0, 0]

    rows = read_rows(ITEMS)
    assert len(rows) == 20
    assert all(row["done"] for row in rows)
