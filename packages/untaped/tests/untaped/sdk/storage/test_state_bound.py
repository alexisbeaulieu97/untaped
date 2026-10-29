"""A plugin class's state rows: its own stamped state.yml, keyed by the field marked Key."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import pytest
import yaml
from pydantic import BaseModel, Field

from core_test_support.runtime.run import run_owning
from untaped.runtime.composition import PluginEntry, compose
from untaped.runtime.declaration import Declaration
from untaped.sdk import Command, ConfigError, Key, Plugin, StateRows, UntapedError
from untaped.sdk._run import Run, run_scope
from untaped.sdk.fields import Positional
from untaped.sdk.projects import Project


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


ITEMS = StateRows[Item]()
CHECKOUTS = StateRows[Item].per_project()


class Remove(Command):
    """Remove an item."""

    removes = ITEMS

    name: Positional[str] = Field(description="The row's key.")

    def run(self) -> None:
        return None


class Forget(Command):
    """Forget a checkout."""

    removes = CHECKOUTS

    name: Positional[str] = Field(description="The row's key.")

    def run(self) -> None:
        return None


class Demo(Plugin):
    """Demo."""

    settings = Settings
    commands = (Remove, Forget)
    items = ITEMS
    notes = StateRows[Note]()
    checkouts = CHECKOUTS


def _composed(holder: type[Plugin]) -> Declaration:
    return compose([PluginEntry("demo", f"{__name__}:{holder.__name__}", holder)])


@pytest.fixture(autouse=True)
def run(tmp_path: Path) -> Iterator[Run]:
    composition = replace(
        run_owning(tmp_path, __name__.partition(".")[0], "demo").composition,
        remove_line=_composed(Demo).remove_line,
    )
    with run_scope(replace(run_owning(tmp_path, "", ""), composition=composition)) as active:
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
    assert (
        caught.value.hint == "correct that row by hand, or remove it with `untaped demo remove ada`"
    )


def test_the_remover_quotes_a_key_for_the_shell() -> None:
    line = _composed(Demo).remove_line("demo", "items", "my notes")
    assert line == "untaped demo remove 'my notes'"


def test_a_damaged_list_row_is_named_by_its_place(tmp_path: Path) -> None:
    folder = tmp_path / "plugins" / "demo"
    folder.mkdir(parents=True)
    (folder / "state.yml").write_text("notes:\n- text: one\n- text: [two]\n")
    with pytest.raises(ConfigError, match=r"demo\.notes row #2 .* is damaged") as caught:
        Demo.notes.all()
    assert caught.value.hint == "correct or remove that row by hand"


def test_rows_the_run_did_not_compose_leave_the_hand_fix(tmp_path: Path, run: Run) -> None:
    def unknown(plugin: str, rows: str, key: str) -> str:
        raise LookupError(rows)

    folder = tmp_path / "plugins" / "demo"
    folder.mkdir(parents=True)
    (folder / "state.yml").write_text("items:\n- name: ada\n  size: lots\n")
    composition = replace(run.composition, remove_line=unknown)
    with run_scope(replace(run, composition=composition)), pytest.raises(ConfigError) as caught:
        Demo.items.get("ada")
    assert caught.value.hint == "correct or remove that row by hand"


def test_the_remover_is_recorded_on_the_rows_it_removes() -> None:
    [plugin] = _composed(Demo).plugins
    removed_by = {block.attribute: block.removed_by for block in plugin.blocks}
    assert removed_by == {
        "items": f"{__name__}:Remove",
        "notes": None,
        "checkouts": f"{__name__}:Forget",
    }


def test_a_remover_takes_the_key_under_the_key_field_s_name() -> None:
    rows = StateRows[Item]()

    class Drop(Command):
        """Drop an item."""

        removes = rows

        id: str = Field(description="The row's id.")

        def run(self) -> None:
            return None

    class Keyed(Plugin):
        """Keyed."""

        settings = Settings
        commands = (Drop,)
        items = rows

    [quarantined] = _composed(Keyed).quarantined
    [problem] = quarantined.problems
    assert (problem.rule, problem.where.rpartition("<locals>.")[2]) == (
        "remover-key",
        "Drop.removes",
    )
    assert "keyed by 'name', but takes no top-level field 'name'" in problem.detail


def test_a_remover_taking_the_key_as_a_later_positional_is_refused() -> None:
    rows = StateRows[Item]()

    class Drop(Command):
        """Drop an item."""

        removes = rows

        scope: Positional[str] = Field(description="Where to drop.")
        name: Positional[str] = Field(description="The row's key.")

        def run(self) -> None:
            return None

    class Late(Plugin):
        """Late."""

        settings = Settings
        commands = (Drop,)
        items = rows

    [quarantined] = _composed(Late).quarantined
    assert [p.rule for p in quarantined.problems] == ["remover-key"]


def test_a_remover_taking_the_key_as_an_option_is_offered_with_its_flag() -> None:
    rows = StateRows[Item]()

    class Drop(Command):
        """Drop an item."""

        removes = rows

        name: str = Field(description="The row's key.")

        def run(self) -> None:
            return None

    class Optioned(Plugin):
        """Optioned."""

        settings = Settings
        commands = (Drop,)
        items = rows

    line = _composed(Optioned).remove_line("demo", "items", "a b")
    assert line == "untaped demo drop --name 'a b'"


def test_rows_have_one_remover_and_it_names_rows_the_plugin_holds() -> None:
    rows = StateRows[Item]()

    class Drop(Command):
        """Drop an item."""

        removes = rows

        name: Positional[str] = Field(description="The row's key.")

        def run(self) -> None:
            return None

    class Delete(Drop):
        """Delete an item."""

    class Stray(Command):
        """Remove rows nobody holds."""

        removes = StateRows[Item]()

        def run(self) -> None:
            return None

    class Twice(Plugin):
        """Twice."""

        settings = Settings
        commands = (Drop, Delete, Stray)
        items = rows

    [quarantined] = _composed(Twice).quarantined
    assert sorted((p.rule, p.where.rpartition("<locals>.")[2]) for p in quarantined.problems) == [
        ("duplicate-remover", "Twice.items"),
        ("reserved-name", "Stray.removes"),
    ]


@pytest.mark.parametrize("method", ["add", "put", "update"])
def test_keyed_writes_on_list_rows_say_the_rows_are_a_list(method: str) -> None:
    calls = {
        "add": lambda: Demo.notes.add(Note(text="one")),  # type: ignore[arg-type]
        "put": lambda: Demo.notes.put(Note(text="one")),
        "update": lambda: Demo.notes.update("one", lambda note: note),
    }
    with pytest.raises(TypeError, match=f"rows are a list .* which {method} cannot"):
        calls[method]()


def test_a_write_that_changes_nothing_leaves_the_file_alone(tmp_path: Path) -> None:
    path = tmp_path / "plugins" / "demo" / "state.yml"
    Demo.notes.replace_all(lambda _: [])
    assert not path.exists()
    Demo.items.put(Item(name="ada"))
    path.write_text(path.read_text())  # same content, a known mtime
    before = path.stat().st_mtime_ns
    Demo.items.put(Item(name="ada"))
    assert path.stat().st_mtime_ns == before


def test_a_file_without_a_stamp_is_read_and_stamped_on_write(tmp_path: Path) -> None:
    folder = tmp_path / "plugins" / "demo"
    folder.mkdir(parents=True)
    (folder / "state.yml").write_text("items:\n- name: ada\n")
    assert Demo.items.keys() == ["ada"]
    Demo.items.put(Item(name="bob"))
    assert _file(tmp_path)["untaped"]["file"] == 1  # type: ignore[index]


def test_a_stamp_that_is_not_a_mapping_is_damaged(tmp_path: Path) -> None:
    folder = tmp_path / "plugins" / "demo"
    folder.mkdir(parents=True)
    (folder / "state.yml").write_text("untaped: 1\nitems: []\n")
    with pytest.raises(ConfigError, match="damaged 'untaped' stamp"):
        Demo.items.keys()


def test_a_failed_write_is_a_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from untaped.sdk.storage import state

    def refuse(_data: object, path: Path) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(state, "write_state_dict", refuse)
    with pytest.raises(ConfigError, match=r"could not write .*Permission denied; nothing changed"):
        Demo.items.add(Item(name="ada"))


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
    declaration = _composed(holder)
    [quarantined] = declaration.quarantined
    return [(p.rule, p.detail) for p in quarantined.problems]


def test_composition_refuses_rows_it_cannot_key() -> None:
    class Broken(Plugin):
        """Broken."""

        settings = Settings
        twice = StateRows[Twice]()
        bare = StateRows()  # type: ignore[var-annotated]

    problems = _shape_problems(Broken)
    assert ("block-shape", "Twice marks a and b Key; a row has one key") in problems
    assert any("name the rows' model" in detail for _, detail in problems)


def test_state_rows_cannot_be_named_after_the_stamp() -> None:
    class Stamped(Plugin):
        """Stamped."""

        settings = Settings
        untaped = StateRows[Note]()

    assert [rule for rule, _ in _shape_problems(Stamped)] == ["block-name"]


def test_a_damaged_project_row_offers_its_remover(tmp_path: Path) -> None:
    project = Project(id="0123456789ab", root=tmp_path / "proj")
    folder = project.root / ".untaped" / "plugins" / "demo"
    folder.mkdir(parents=True)
    (folder / "state.yml").write_text("checkouts:\n- name: ada\n  size: lots\n")
    with pytest.raises(ConfigError) as caught:
        Demo.checkouts.of(project).get("ada")
    assert (
        caught.value.hint == "correct that row by hand, or remove it with `untaped demo forget ada`"
    )


def test_the_11x_spelling_keeps_its_repr_and_compares_by_name() -> None:
    first = StateRows("acme", "spelled_things", model=Item, key_field="name")
    again = StateRows("acme", "spelled_things", model=Item, key_field="name")
    assert repr(first) == "StateRows('acme', 'spelled_things')"
    assert first == again


def test_a_subscripted_11x_per_project_keeps_its_spelling() -> None:
    rows = StateRows[Item].per_project("acme", "spelled_checkouts", model=Item, key_field="name")
    assert rows.spelled
    assert repr(rows) == "ProjectStateRows('acme', 'spelled_checkouts')"


def test_the_state_file_stamps_as_every_data_file_does() -> None:
    from untaped.sdk.storage import data_file, state

    assert state.STAMP == data_file.STAMP


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: StateRows(key_field="name"), "not key_field= or remove_hint="),
        (lambda: StateRows("acme", "x", model=Item), "the 11.x spelling takes"),
        (lambda: StateRows(model="Item"), "model takes a model class"),  # type: ignore[arg-type]
        (lambda: StateRows.per_project(remove_hint="rm {key}"), "not key_field="),
        (lambda: StateRows.per_project("acme", "x"), "the 11.x spelling takes"),
        (lambda: StateRows.per_project(model="Item"), "model takes a model class"),  # type: ignore[arg-type]
    ],
)
def test_a_misspelled_declaration_is_refused(build: Callable[[], object], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        build()


def test_rows_a_plugin_class_holds_are_named_where_they_were_made() -> None:
    assert repr(Demo.items) == f"StateRows('StateRows created in {__name__}')"
    assert repr(Demo.checkouts) == f"ProjectStateRows('ProjectStateRows created in {__name__}')"
    assert Demo.items == Demo.items
    assert Demo.items != Demo.notes


def test_composition_refuses_rows_per_project_it_cannot_key() -> None:
    def refusing() -> type[Item]:
        raise ConfigError("no model today")

    class Broken(Plugin):
        """Broken."""

        settings = Settings
        twice = StateRows[Twice].per_project()
        notmodel = StateRows(model=lambda: int)  # type: ignore[arg-type,return-value]
        refused = StateRows(model=refusing)

    problems = _shape_problems(Broken)
    assert ("block-shape", "Twice marks a and b Key; a row has one key") in problems
    assert ("block-shape", "model is not a pydantic model: <class 'int'>") in problems
    assert ("block-shape", "no model today") in problems


def test_list_rows_have_one_remover_too() -> None:
    notes = StateRows[Note]()

    class Clear(Command):
        """Clear the notes."""

        removes = notes

        def run(self) -> None:
            return None

    class Wipe(Clear):
        """Wipe the notes."""

    class Listed(Plugin):
        """Listed."""

        settings = Settings
        commands = (Clear, Wipe)
        log = notes

    [quarantined] = _composed(Listed).quarantined
    assert [(p.rule, p.where.rpartition("<locals>.")[2]) for p in quarantined.problems] == [
        ("duplicate-remover", "Listed.log")
    ]
