"""A plugin class's state rows: its own stamped state.yml, keyed by the field marked Key."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import pytest
import yaml
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.runtime.composition import PluginEntry, compose
from untaped.sdk import Command, ConfigError, Key, Plugin, StateRows, UntapedError
from untaped.sdk.projects import Project
from untaped.sdk.run import Run, run_scope


class Item(BaseModel):
    name: Annotated[str, Key]
    size: int = 0


class Note(BaseModel):
    text: str


class Twice(BaseModel):
    a: Annotated[str, Key]
    b: Annotated[str, Key]


class Settings(BaseModel):
    pass


class Remove(Command):
    """Remove an item."""

    def run(self) -> None:
        return None


class Demo(Plugin):
    """Demo."""

    settings = Settings
    commands = (Remove,)
    items = StateRows[Item](remove=Remove)
    notes = StateRows[Note]()
    checkouts = StateRows[Item].per_project()


@pytest.fixture(autouse=True)
def run(tmp_path: Path) -> Iterator[Run]:
    with run_scope(run_owning(tmp_path, __name__.partition(".")[0], "demo")) as active:
        yield active


def _file(tmp_path: Path) -> dict[str, object]:
    loaded = yaml.safe_load((tmp_path / "plugins" / "demo" / "state.yml").read_text())
    assert isinstance(loaded, dict)
    return loaded


def test_keyed_rows_live_in_the_plugins_own_stamped_file(tmp_path: Path) -> None:
    Demo.items.add(Item(name="ada", size=1))
    Demo.items.put(Item(name="bob"))
    Demo.items.update("ada", lambda item: item.model_copy(update={"size": 2}))
    assert Demo.items.get("ada") == Item(name="ada", size=2)
    assert Demo.items.keys() == ["ada", "bob"]
    assert Demo.items.delete("bob") == 1
    stored = _file(tmp_path)
    assert stored["items"] == [{"name": "ada", "size": 2}]
    assert stored["untaped"] == {"file": 1, "by": stored["untaped"]["by"]}  # type: ignore[index]
    assert Demo.items.path() == tmp_path / "plugins" / "demo" / "state.yml"


def test_rows_without_a_key_are_a_list(tmp_path: Path) -> None:
    Demo.notes.append(Note(text="one"))
    Demo.notes.append(Note(text="one"))
    assert Demo.notes.all() == [Note(text="one"), Note(text="one")]
    Demo.notes.replace_all(lambda notes: notes[:1])
    assert _file(tmp_path)["notes"] == [{"text": "one"}]
    with pytest.raises(TypeError, match="rows are a list"):
        Demo.notes.get("one")
    with pytest.raises(TypeError, match="rows are keyed"):
        Demo.items.append(Item(name="x"))


def test_a_damaged_row_offers_the_remove_command(tmp_path: Path) -> None:
    folder = tmp_path / "plugins" / "demo"
    folder.mkdir(parents=True)
    (folder / "state.yml").write_text("items:\n- name: ada\n  size: lots\n")
    with pytest.raises(ConfigError, match=r"demo\.items row ada .* is damaged") as caught:
        Demo.items.get("ada")
    assert caught.value.hint == "correct that row by hand, or remove it with `Remove ada`"


def test_a_newer_untapeds_file_is_never_overwritten(tmp_path: Path) -> None:
    folder = tmp_path / "plugins" / "demo"
    folder.mkdir(parents=True)
    (folder / "state.yml").write_text("untaped: {file: 2, by: '13.0.0'}\nitems: []\n")
    with pytest.raises(ConfigError, match="written by a newer untaped"):
        Demo.items.add(Item(name="ada"))
    assert "13.0.0" in (folder / "state.yml").read_text()


def test_a_write_inside_a_write_is_refused() -> None:
    def nested(rows: list[Item]) -> list[Item]:
        Demo.items.add(Item(name="inner"))
        return rows

    with pytest.raises(UntapedError, match="inside another write"):
        Demo.items.replace_all(nested)


def test_a_projects_rows_live_under_its_plugin_folder(tmp_path: Path) -> None:
    project = Project(id="0123456789ab", root=tmp_path / "proj")
    rows = Demo.checkouts.of(project)
    rows.add(Item(name="ada"))
    path = tmp_path / "proj" / ".untaped" / "plugins" / "demo" / "state.yml"
    assert rows.path() == path
    assert yaml.safe_load(path.read_text())["checkouts"] == [{"name": "ada", "size": 0}]


def _shape_problems(holder: type[Plugin]) -> list[tuple[str, str]]:
    declaration = compose([PluginEntry("demo", f"{__name__}:{holder.__name__}", holder)])
    [quarantined] = declaration.quarantined
    return [(p.rule, p.detail) for p in quarantined.problems]


def test_composition_refuses_rows_it_cannot_key() -> None:
    class Broken(Plugin):
        """Broken."""

        settings = Settings
        twice = StateRows[Twice]()
        bare = StateRows()  # type: ignore[var-annotated]

    problems = _shape_problems(Broken)
    assert (
        "block-shape",
        "state rows StateRows created in "
        + __name__
        + ": Twice marks a and b Key; a row has one key",
    ) in problems
    assert any("name the rows' model" in detail for _, detail in problems)


def test_state_rows_cannot_be_named_after_the_stamp() -> None:
    class Stamped(Plugin):
        """Stamped."""

        settings = Settings
        untaped = StateRows[Note]()

    assert [rule for rule, _ in _shape_problems(Stamped)] == ["block-name"]


def test_the_state_file_stamps_as_every_data_file_does() -> None:
    from untaped.sdk.storage import data_file, state

    assert state.STAMP == data_file.STAMP
