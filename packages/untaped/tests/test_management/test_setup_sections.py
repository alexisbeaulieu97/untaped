"""The setup screen's sections: rows, sources, help, and editing a setting from its row.

Every screen case drives the real screen (``drive_screen``, commands run
synchronously) over the isolated config, with ``knobs``, a plugin double whose
settings cover each shape. The mapping cases build each shape's field directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Annotated, ClassVar, Literal

import pytest
from pydantic import BaseModel, Field, SecretStr
from rich.console import Console

from screen.support import FakeHost
from test_contracts import support as shelf
from test_management.support import (
    UnmarkedWizProfile,
    WizProfile,
    compose,
    make_spec,
    write_config,
)
from untaped.config.fields import editor, written
from untaped.config.repository import SettingsFileRepository
from untaped.config.use_cases import NamedValue, RankProviders, SetSetting
from untaped.config_file import read_config_dict
from untaped.config_schema import Argv, Choices, FieldDescriptor, walk_settings
from untaped.deprecated_keys import Renamed
from untaped.management.setup_screen import SetupModel, SetupResult, setup_screen
from untaped.management.setup_state import setup_sections, setup_services
from untaped.plugins.registry import PluginSpec, settings_model
from untaped.screen.components.choices import Check, Cycle, Select, SingleList
from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput
from untaped.screen.components.lists import ListEditor, MapEditor
from untaped.screen.core import Frame, Paste, Screen
from untaped.screen.runtime import Runtime
from untaped.stability import deprecated, experimental
from untaped.testing import drive_screen
from untaped.testing.screens import ScreenKey, _message
from untaped.theme import BUILTIN_THEMES

pytestmark = pytest.mark.usefixtures("_isolated_config")


class KnobsSweep(BaseModel):
    parallel: int = Field(default=4, ge=1, description="Workers per sweep.")


class KnobsProfile(BaseModel):
    """A plugin double with a setting of each shape (section ``knobs``)."""

    renamed_keys: ClassVar[Mapping[str, object]] = {"old_size": Renamed("size", removed_in=12)}

    size: int = Field(default=200, gt=0, description="Results per `page`.")
    label: str | None = Field(default=None, description="A label.")
    mode: Literal["fast", "slow"] = Field(default="fast", description="How it runs.")
    tags: list[str] = Field(default_factory=list, description="Tags.")
    probe: Annotated[int, experimental] = Field(default=2, description="An experiment.")
    legacy: Annotated[bool, deprecated(removed_in=12, replacement="knobs.mode")] = Field(
        default=False, description="The old switch."
    )
    sweep: KnobsSweep = Field(default_factory=KnobsSweep)
    secret: SecretStr | None = Field(default=None, description="A secret.")


def _knobs() -> PluginSpec:
    return make_spec("knobs", settings=KnobsProfile)


def _build(
    *specs: PluginSpec, profile: str = "default", only: list[str] | None = None
) -> Screen[SetupModel, SetupResult]:
    result = compose(*(specs or (_knobs(),)))
    raw = read_config_dict()
    return setup_screen(
        result,
        setup_services(result, only),
        sections=setup_sections(only),
        profile=profile,
        store=None,
        profiles=sorted(raw.get("profiles") or ()),
    )


def _to(section: str, *specs: PluginSpec, profile: str = "default") -> list[ScreenKey]:
    """The keys from the list to ``section``'s rows."""
    model, _ = _build(*specs, profile=profile).init()
    index = next(i for i, row in enumerate(model.rows) if row.name == section)
    return [*(["down"] * (index - model.selected)), "enter"]


def _rows(model: SetupModel, section: str = "knobs") -> list[tuple[str, str, str]]:
    return [(row.label, row.value, row.source) for row in model.panes[section].rows]


def _knobs_data(path: Path, profile: str = "work") -> object:
    return read_config_dict(path)["profiles"][profile].get("knobs")


_WORK = (
    "profiles:\n  default:\n    knobs: {label: shared}\n  work:\n    knobs: {size: 50}\n"
    "active: work\n"
)


# --- rows -------------------------------------------------------------------------


def test_rows_say_where_each_value_comes_from(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _WORK)
    monkeypatch.setenv("UNTAPED_KNOBS__MODE", "slow")

    run = drive_screen(_build(profile="work"), _to("knobs", profile="work"), size=(140, 30))

    assert _rows(run.model) == [
        ("size", "50", "profile:work"),
        ("label", "shared", "profile:default"),
        ("mode", "slow", "UNTAPED_KNOBS__MODE"),
        ("tags", "[]", "default"),
        ("probe", "2", "default"),
        ("sweep.parallel", "4", "default"),
        ("secret", "", "unset"),
    ]  # the deprecated `legacy` is listed only once it is set
    assert run.model.panes["knobs"].rows[4].marks == ("experimental",)
    # The help under the rows: the description (as plain text), bounds, default and variable.
    assert "Results per page." in run.frame and "Above 0." in run.frame
    assert "default 200 · UNTAPED_KNOBS__SIZE" in run.frame


def test_a_row_set_by_a_variable_says_the_variable_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_KNOBS__MODE", "slow")

    run = drive_screen(_build(), [*_to("knobs"), "down", "down"], size=(140, 30))

    assert "UNTAPED_KNOBS__MODE is set: it wins over the profile until it is unset." in run.frame


def test_sections_are_listed_services_first_then_core_then_plugins() -> None:
    wiz = make_spec("wiz", settings=WizProfile)
    model, _ = _build(_knobs(), wiz).init()

    assert [row.name for row in model.rows] == [
        "this install",
        "wiz",
        "http",
        "ui",
        "skills",
        "knobs",
    ]


def test_only_lists_the_named_sections() -> None:
    model, _ = _build(only=["ui"]).init()

    assert [row.name for row in model.rows] == ["this install", "ui"]


def test_rows_fit_a_small_terminal() -> None:
    run = drive_screen(_build(), [*_to("knobs"), "down", "down", "down", "down"], size=(80, 24))

    lines = run.frame.splitlines()
    assert len(lines) <= 24 and all(len(line) <= 80 for line in lines)
    assert "experimental" in run.frame  # a mark is a word, not only a colour


# --- editing ----------------------------------------------------------------------


def test_an_edit_writes_what_config_set_writes(_isolated_config: Path) -> None:
    write_config(_isolated_config, _WORK)

    run = drive_screen(
        _build(profile="work"),
        [*_to("knobs", profile="work"), "enter", "ctrl-u", "7", "5", "enter"],
    )
    by_screen = _isolated_config.read_text(encoding="utf-8")
    write_config(_isolated_config, _WORK)
    SetSetting(SettingsFileRepository())("knobs.size", "75", profile="work")

    assert by_screen == _isolated_config.read_text(encoding="utf-8")
    assert run.model.panes["knobs"].editing is None  # the field closed
    assert _rows(run.model)[0] == ("size", "75", "profile:work")
    assert run.model.written == (("work", "knobs.size"),)


def test_an_invalid_edit_shows_config_sets_error_and_writes_nothing(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _WORK)
    before = _isolated_config.read_text(encoding="utf-8")

    run = drive_screen(
        _build(profile="work"),
        [*_to("knobs", profile="work"), "enter", "ctrl-u", "0", "enter"],
        size=(140, 30),
    )

    assert "invalid value for 'knobs.size'" in run.frame
    assert "Input should be greater than 0" in run.frame
    assert _isolated_config.read_text(encoding="utf-8") == before
    assert run.model.written == ()


def test_a_choice_is_picked_then_written(_isolated_config: Path) -> None:
    run = drive_screen(_build(), [*_to("knobs"), "down", "down", "enter", "down", " ", "enter"])

    assert _knobs_data(_isolated_config, "default") == {"mode": "slow"}
    assert _rows(run.model)[2] == ("mode", "slow", "profile:default")


def test_an_empty_field_unsets_the_key(_isolated_config: Path) -> None:
    write_config(_isolated_config, _WORK)

    drive_screen(
        _build(profile="work"), [*_to("knobs", profile="work"), "enter", "ctrl-u", "enter"]
    )

    assert _knobs_data(_isolated_config) is None


def test_esc_closes_a_field_without_writing(_isolated_config: Path) -> None:
    write_config(_isolated_config, _WORK)
    before = _isolated_config.read_text(encoding="utf-8")

    run = drive_screen(_build(profile="work"), [*_to("knobs", profile="work"), "enter", "9", "esc"])

    assert run.model.panes["knobs"].editing is None and run.model.focus == "section"
    assert _isolated_config.read_text(encoding="utf-8") == before


def test_unset_asks_then_falls_back_to_what_the_profile_inherits(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    knobs: {size: 80}\n  work:\n    knobs: {size: 50}\n",
    )
    keys = _to("knobs", profile="work")

    asked = drive_screen(_build(profile="work"), [*keys, "delete"], size=(140, 30))
    kept = drive_screen(_build(profile="work"), [*keys, "delete", "n"])
    run = drive_screen(_build(profile="work"), [*keys, "backspace", "y"])

    assert "Unset size in profile work? y unsets it" in asked.frame
    assert _rows(kept.model)[0] == ("size", "50", "profile:work")
    assert _rows(run.model)[0] == ("size", "80", "profile:default")
    assert _knobs_data(_isolated_config) is None


def test_delete_does_nothing_on_a_row_the_profile_does_not_set() -> None:
    run = drive_screen(_build(), [*_to("knobs"), "delete"])

    assert run.model.panes["knobs"].confirm is False


def test_a_list_is_edited_in_its_editor_and_written_whole(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {tags: [a, b, c]}\n")
    to_tags = [*_to("knobs"), "down", "down", "down", "enter"]

    # Remove ``b``, move ``a`` below ``c``, add ``d``, then enter on ``done``.
    keys = [*to_tags, "down", "delete", "up", "m", "down", "enter", "down", "enter", "d", "enter"]
    run = drive_screen(_build(), [*keys, "down", "enter"], size=(140, 40))

    moving = drive_screen(_build(), [*to_tags, "m"])
    assert "enter put down" in moving.frame.splitlines()[-1]
    assert _knobs_data(_isolated_config, "default") == {"tags": ["c", "a", "d"]}
    assert run.model.written == (("default", "knobs.tags"),)
    assert run.model.panes["knobs"].editing is None

    drive_screen(_build(), [*to_tags, "delete", "ctrl-s"])  # ctrl-s writes as ``done`` does

    assert _knobs_data(_isolated_config, "default") == {"tags": ["a", "d"]}


def test_a_list_editor_keeps_delete_and_esc_while_an_item_is_open(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {tags: [ab]}\n")
    keys = [*_to("knobs"), "down", "down", "down", "enter", "enter", "backspace", "esc"]

    run = drive_screen(_build(), keys)

    pane = run.model.panes["knobs"]
    assert pane.editing is not None and pane.editing.value == ("ab",)  # only the edit dropped
    assert pane.confirm is False


def test_ctrl_s_keeps_the_open_item_and_writes_the_list(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {tags: [a]}\n")
    keys = [*_to("knobs"), "down", "down", "down", "enter", "down", "enter", "b", "ctrl-s"]

    drive_screen(_build(), keys)

    assert _knobs_data(_isolated_config, "default") == {"tags": ["a", "b"]}


def test_done_closes_an_unchanged_list_and_says_so(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {tags: [a]}\n")
    keys = [*_to("knobs"), "down", "down", "down", "enter", "down", "down"]

    run = drive_screen(_build(), keys)

    assert "enter close" in run.frame.splitlines()[-1]


def test_a_list_a_variable_sets_opens_with_its_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_KNOBS__TAGS", '["from-env"]')

    run = drive_screen(_build(), [*_to("knobs"), "down", "down", "down", "enter"])

    editing = run.model.panes["knobs"].editing
    assert isinstance(editing, ListEditor) and editing.value == ("from-env",)


@pytest.mark.parametrize(
    ("width", "height", "error"),
    [(58, 12, ""), (78, 12, ""), (58, 10, ""), (40, 16, ""), (58, 14, "x " * 60)],
)
def test_an_open_list_editor_fits_its_pane_with_its_help(
    _isolated_config: Path, width: int, height: int, error: str
) -> None:
    tags = ", ".join(f"t{index}" for index in range(20))
    write_config(_isolated_config, f"profiles:\n  default:\n    knobs: {{tags: [{tags}]}}\n")
    run = drive_screen(_build(), [*_to("knobs"), "down", "down", "down", "enter"])
    pane = run.model.panes["knobs"].with_error(error)

    frame = Frame(width, height, BUILTIN_THEMES["default"])
    drawn = Console(width=width, record=True).render_lines(
        pane.view(frame, focused=True), pad=False
    )

    assert len(drawn) <= height
    assert "writes" in "".join(segment.text for segment in drawn[-1])  # its help ends the pane


def test_an_empty_list_overrides_an_inherited_one(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    knobs: {tags: [a]}\n  work: {}\n",
    )
    keys = [*_to("knobs", profile="work"), "down", "down", "down", "enter", "delete", "ctrl-s"]

    drive_screen(_build(profile="work"), keys)

    assert _knobs_data(_isolated_config) == {"tags": []}


def test_a_renamed_key_in_use_is_written_under_its_new_name(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {old_size: 70}\n")

    shown = drive_screen(_build(), _to("knobs"), size=(140, 30))
    run = drive_screen(_build(), [*_to("knobs"), "enter", "ctrl-u", "7", "1", "enter"])

    assert _rows(shown.model)[0] == ("size", "70", "profile:default")
    assert "from knobs.old_size" in shown.frame
    assert _knobs_data(_isolated_config, "default") == {"size": 71}
    assert any("removed knobs.old_size" in text for _, text in run.model.notes)


def test_a_deprecated_setting_is_read_only_but_unsets(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {legacy: true}\n")
    keys = [*_to("knobs"), *(["down"] * 5)]

    shown = drive_screen(_build(), [*keys, "enter"], size=(140, 30))
    run = drive_screen(_build(), [*keys, "delete", "y"])

    row = shown.model.panes["knobs"].rows[5]
    assert (row.label, row.marks, row.descriptor) == ("legacy", ("deprecated",), None)
    assert shown.model.panes["knobs"].editing is None and shown.commands_run == ()
    assert "Read-only here (deprecated)" in shown.frame
    assert "legacy" not in [label for label, _, _ in _rows(run.model)]
    assert _knobs_data(_isolated_config, "default") is None


def test_an_invalid_section_is_repaired_from_its_rows(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {size: lots}\n")

    shown = drive_screen(_build(), _to("knobs"), size=(140, 30))
    run = drive_screen(_build(), [*_to("knobs"), "enter", "ctrl-u", "6", "0", "enter"])

    assert next(r.status for r in shown.model.rows if r.name == "knobs") == "invalid settings"
    assert "Input should be a valid integer" in shown.frame
    assert _knobs_data(_isolated_config, "default") == {"size": 60}
    assert next(r.status for r in run.model.rows if r.name == "knobs") == ""
    assert run.model.panes["knobs"].error == ""


def test_a_value_changed_while_its_field_was_open_is_not_overwritten(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _WORK)
    host = FakeHost()
    runtime = Runtime(_build(profile="work"), host, theme=BUILTIN_THEMES["default"], size=(140, 30))
    runtime.start()
    for key in [*_to("knobs", profile="work"), "enter"]:
        runtime.send(_message(key))
    host.run_jobs()
    host.deliver()  # the field is open, holding 50

    SetSetting(SettingsFileRepository())("knobs.size", "51", profile="work")  # another writer
    for key in ["ctrl-u", "9", "enter"]:
        runtime.send(_message(key))
    host.run_jobs()
    host.deliver()

    assert _knobs_data(_isolated_config) == {"size": 51}
    pane = runtime.model.panes["knobs"]
    assert "knobs.size changed in profile work while it was open" in pane.error
    assert _rows(runtime.model)[0] == ("size", "51", "profile:work")  # re-read
    assert runtime.model.written == ()


def test_a_list_changed_while_its_editor_was_open_is_not_overwritten(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {tags: [a]}\n")
    host = FakeHost()
    runtime = Runtime(_build(), host, theme=BUILTIN_THEMES["default"], size=(140, 30))
    runtime.start()
    for key in [*_to("knobs"), "down", "down", "down", "enter"]:
        runtime.send(_message(key))
    host.run_jobs()
    host.deliver()  # the editor is open, holding [a]

    SetSetting(SettingsFileRepository())("knobs.tags", '["x"]')  # another writer
    for key in ["down", "enter", "b", "enter", "ctrl-s"]:
        runtime.send(_message(key))
    host.run_jobs()
    host.deliver()

    assert _knobs_data(_isolated_config, "default") == {"tags": ["x"]}
    assert "knobs.tags changed in profile default while it was open" in (
        runtime.model.panes["knobs"].error
    )
    assert runtime.model.written == ()


def test_a_new_profile_shows_defaults_and_is_created_by_its_first_write(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {size: 80}\n")

    shown = drive_screen(_build(profile="fresh"), _to("knobs", profile="fresh"), size=(140, 30))
    run = drive_screen(
        _build(profile="fresh"),
        [*_to("knobs", profile="fresh"), "enter", "ctrl-u", "9", "enter"],
    )

    assert "new profile: its first write creates it" in shown.frame
    assert _rows(shown.model)[0] == ("size", "80", "profile:default")
    assert _knobs_data(_isolated_config, "fresh") == {"size": 9}
    assert ("success", "created profile: fresh") in run.model.notes
    assert "new profile" not in run.frame


def test_quitting_reports_each_written_key() -> None:
    run = drive_screen(_build(), [*_to("knobs"), "enter", "ctrl-u", "9", "enter", "esc", "esc"])

    assert run.result is not None
    assert run.result.written == ("knobs.size",)
    assert run.result.touched == ()
    assert any(text.startswith("set knobs.size in profile default") for _, text in run.result.notes)


@pytest.mark.parametrize(
    ("variable", "down"),
    [(None, 0), ("33", 0), (None, 1)],
    ids=["default", "variable", "unset"],
)
def test_a_field_written_back_untouched_writes_nothing(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch, variable: str | None, down: int
) -> None:
    if variable is not None:
        monkeypatch.setenv("UNTAPED_KNOBS__SIZE", variable)
    write_config(_isolated_config, "profiles:\n  default: {}\n  work: {}\n")
    before = _isolated_config.read_text(encoding="utf-8")
    keys = [*_to("knobs", profile="work"), *(["down"] * down), "enter", "enter", "esc", "esc"]

    run = drive_screen(_build(profile="work"), keys)

    assert _isolated_config.read_text(encoding="utf-8") == before
    assert run.result is not None
    assert run.result.written == () and run.result.notes == ()


def test_an_empty_secret_field_keeps_the_stored_secret(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {secret: s3cr3t}\n")
    before = _isolated_config.read_text(encoding="utf-8")

    keys = [*_to("knobs"), "end", "enter", "enter"]
    run = drive_screen(_build(), keys)

    assert run.commands_run == ("open_field",)
    assert _isolated_config.read_text(encoding="utf-8") == before
    assert run.model.panes["knobs"].editing is None


@pytest.mark.parametrize("typed", ["0", ""], ids=["invalid", "empty"])
def test_a_write_that_fails_or_unsets_leaves_a_new_profile_uncreated(
    _isolated_config: Path, typed: str
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    knobs: {size: 80}\n")
    keys = [*_to("knobs", profile="fresh"), "enter", "ctrl-u", *typed, "enter"]

    run = drive_screen(_build(profile="fresh"), keys)

    assert "fresh" not in read_config_dict(_isolated_config)["profiles"]
    assert run.model.written == () and run.model.notes == ()


def test_delete_unsets_the_profiles_value_under_a_variable(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _WORK)
    monkeypatch.setenv("UNTAPED_KNOBS__SIZE", "33")

    run = drive_screen(_build(profile="work"), [*_to("knobs", profile="work"), "delete", "y"])

    assert _knobs_data(_isolated_config) is None
    assert run.model.written == (("work", "knobs.size"),)


# --- the field of each shape ------------------------------------------------------


class Shapes(BaseModel):
    flag: bool = True
    maybe: bool | None = None
    count: int = 1
    ratio: float = 0.5
    text: str = "x"
    where: Path = Path("/tmp")
    pick: Literal["a", "b"] = "a"
    many: Literal["a", "b", "c", "d", "e"] = "a"
    optional_pick: Literal["a", "b"] | None = None
    named: Annotated[str, Choices(lambda: ("one", "two"), "names")] = "one"
    secret: SecretStr | None = None
    items: list[str] = []
    argv: Annotated[list[str] | None, Argv()] = None
    table: dict[str, int] = {}
    marks: Annotated[dict[str, str], Choices(lambda: ("a", "b"), "marks")] = {}


def _descriptor(name: str) -> FieldDescriptor:
    return next(d for d in walk_settings(Shapes, include_collections=True) if d.key == name)


@pytest.mark.parametrize(
    ("name", "value", "kind"),
    [
        ("flag", True, Check),
        ("maybe", None, Cycle),
        ("count", 3, NumberInput),
        ("ratio", 0.5, NumberInput),
        ("text", "x", TextInput),
        ("where", "/tmp", PathInput),
        ("pick", "a", SingleList),
        ("many", "a", Select),
        ("named", "one", SingleList),
        ("secret", None, SecretInput),
        ("items", ["a"], ListEditor),
        ("argv", ["gh", "auth", "token"], TextInput),
        ("table", {"a": 1}, TextInput),
    ],
)
def test_each_shape_gets_its_field(name: str, value: object, kind: type) -> None:
    component = editor(_descriptor(name), value, label=name)

    assert isinstance(component, kind)


def test_a_list_opens_its_items_an_argv_a_command_line_and_a_map_its_json() -> None:
    items = editor(_descriptor("items"), ["a", "b"], label="items")
    argv = editor(_descriptor("argv"), ["pass", "show", "my token"], label="argv")
    table = editor(_descriptor("table"), {"a": 1}, label="table")

    assert items.value == ("a", "b")
    assert argv.value == "pass show 'my token'"
    assert table.value == '{"a": 1}'


def test_a_map_that_declares_its_names_edits_the_profiles_own_names() -> None:
    names = (NamedValue("a", "1", "default"), NamedValue("b", "2", "profile:default"))
    marks = editor(_descriptor("marks"), names, label="marks", own={"b": "3"}, own_source="p:x")
    assert isinstance(marks, MapEditor)
    cleared = replace(marks, own=())

    assert marks.value == {"b": "3"}
    assert [entry.name for entry in marks.entries] == ["a", "b"]
    assert written(_descriptor("marks"), marks) == '{"b": "3"}'
    assert written(_descriptor("marks"), cleared) is None  # no names left: unset


@pytest.mark.parametrize("model", [UnmarkedWizProfile, WizProfile])
def test_a_token_command_is_declared_a_command(model: type[BaseModel]) -> None:
    # A marked model's command setting is the one core adds to the composed section.
    composed = settings_model(make_spec("wiz", settings=model))
    descriptors = walk_settings(composed, include_collections=True)

    assert [d.key for d in descriptors if d.argv] == ["token_command"]


def test_a_malformed_command_line_is_the_fields_own_error() -> None:
    argv = editor(_descriptor("argv"), None, label="argv")
    assert isinstance(argv, TextInput)

    assert argv.validator is not None
    assert argv.validator("gh 'auth").startswith("not a command line")
    assert argv.validator("gh auth token") == ""


def test_a_collection_its_section_failed_to_validate_opens_as_json() -> None:
    component = editor(_descriptor("items"), '["a", 1]', label="items", raw=True)

    assert isinstance(component, TextInput) and component.value == '["a", 1]'
    assert written(_descriptor("items"), component, raw=True) == '["a", 1]'


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("flag", True, "true"),
        ("maybe", False, "false"),
        ("maybe", None, None),
        ("count", 3, "3"),
        ("text", "", None),  # empty unsets
        ("where", "~/x", "~/x"),
        ("pick", "b", "b"),
        ("optional_pick", "", None),  # the ``unset`` choice
        ("items", ["a", "b"], '["a", "b"]'),
        ("items", [], "[]"),  # an empty list is written, overriding an inherited one
        ("argv", ["gh", "auth x"], '["gh", "auth x"]'),
        ("argv", None, None),  # an empty command line unsets
        ("table", {"a": 1}, '{"a": 1}'),
    ],
)
def test_a_field_writes_what_config_set_takes(name: str, value: object, expected: object) -> None:
    descriptor = _descriptor(name)
    component = editor(descriptor, value, label=name)
    assert component is not None

    assert written(descriptor, component) == expected


def test_an_optional_choice_offers_unset_and_choices_come_from_the_marker() -> None:
    optional = editor(_descriptor("optional_pick"), None, label="optional_pick")
    named = editor(_descriptor("named"), "two", label="named")

    assert isinstance(optional, SingleList) and optional.items[0].label == "unset"
    assert isinstance(named, SingleList) and [i.id for i in named.items] == ["one", "two"]


def test_a_value_its_section_failed_to_validate_opens_as_text() -> None:
    component = editor(_descriptor("count"), "lots", label="count", raw=True)

    assert isinstance(component, TextInput) and component.value == "lots"
    assert written(_descriptor("count"), component, raw=True) == "lots"


def test_a_secret_field_starts_empty_and_writes_what_is_typed() -> None:
    component = editor(_descriptor("secret"), "***", label="secret")
    assert isinstance(component, SecretInput)
    assert component.value.get_secret_value() == ""

    typed = SecretInput("secret", value=SecretStr("s3cr3t"))
    assert written(_descriptor("secret"), typed) == "s3cr3t"


# --- maps that declare their names ------------------------------------------------

_SYMBOLS = (
    "profiles:\n  default:\n    ui: {symbols: {error: X}}\n"
    "  work:\n    ui: {symbols: {success: S}}\nactive: work\n"
)


def _ui(profile: str = "work") -> Screen[SetupModel, SetupResult]:
    return _build(profile=profile, only=["ui"])


def _to_symbols(profile: str = "work") -> list[ScreenKey]:
    """The keys from the list into the open ``ui.symbols`` editor."""
    model, _ = _ui(profile).init()
    section = next(i for i, row in enumerate(model.rows) if row.name == "ui")
    keys: list[ScreenKey] = [*(["down"] * (section - model.selected)), "enter"]
    rows = drive_screen(_ui(profile), keys).model.panes["ui"].rows
    index = next(i for i, row in enumerate(rows) if row.label == "symbols")
    return [*keys, *(["down"] * index), "enter"]


def _ui_data(path: Path, profile: str = "work") -> object:
    return read_config_dict(path)["profiles"][profile].get("ui")


def test_a_map_row_counts_its_overrides(_isolated_config: Path) -> None:
    write_config(_isolated_config, _SYMBOLS)

    run = drive_screen(_ui(), _to_symbols()[:-1], size=(140, 40))

    row = next(row for row in run.model.panes["ui"].rows if row.label == "symbols")
    assert row.value == "1 override"  # the profile's own; ``error`` is the default profile's


def test_a_symbol_is_edited_in_place_and_the_profiles_own_map_written(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _SYMBOLS)
    # ``warning`` (second) takes ``!``; enter on ``done`` writes the map.
    keys = [*_to_symbols(), "down", "enter", "ctrl-u", "!", "enter", "end", "enter"]

    run = drive_screen(_ui(), keys, size=(140, 40))

    assert _ui_data(_isolated_config) == {"symbols": {"success": "S", "warning": "!"}}
    assert run.model.written == (("work", "ui.symbols"),)


def test_an_override_cleared_shows_what_it_inherits_and_the_last_one_unsets(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _SYMBOLS.replace("success: S", "success: S, error: E"))
    to_error = [*_to_symbols(), "down", "down"]

    cleared = drive_screen(_ui(), [*to_error, "delete"], size=(140, 40))
    drive_screen(_ui(), [*to_error, "delete", "home", "delete", "ctrl-s"])

    assert "profile:default" in cleared.frame  # ``error`` is the default profile's X again
    assert _ui_data(_isolated_config) is None  # unset, so the profile inherits the whole map


def test_an_inherited_value_opened_and_kept_is_not_copied(_isolated_config: Path) -> None:
    write_config(_isolated_config, _SYMBOLS)
    keys = [*_to_symbols(), "down", "down", "enter", "enter", "end"]

    run = drive_screen(_ui(), keys, size=(140, 40))

    assert "enter close" in run.frame.splitlines()[-1]
    assert run.model.panes["ui"].editing is not None
    assert run.model.panes["ui"].editing.value == {"success": "S"}


def test_a_map_editor_footer_offers_edit_then_keep(_isolated_config: Path) -> None:
    write_config(_isolated_config, _SYMBOLS)

    on_name = drive_screen(_ui(), _to_symbols(), size=(140, 40))
    editing = drive_screen(_ui(), [*_to_symbols(), "enter"], size=(140, 40))

    assert "enter edit" in on_name.frame.splitlines()[-1]
    assert "enter keep" in editing.frame.splitlines()[-1]


def test_a_map_changed_while_its_editor_was_open_is_not_overwritten(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _SYMBOLS)
    host = FakeHost()
    runtime = Runtime(_ui(), host, theme=BUILTIN_THEMES["default"], size=(140, 40))
    runtime.start()
    for key in _to_symbols():
        runtime.send(_message(key))
    host.run_jobs()
    host.deliver()  # the editor is open, holding {success: S}

    SetSetting(SettingsFileRepository())("ui.symbols", '{"info": "i"}', profile="work")
    for key in ["down", "enter", "!", "enter", "ctrl-s"]:
        runtime.send(_message(key))
    host.run_jobs()
    host.deliver()

    assert _ui_data(_isolated_config) == {"symbols": {"info": "i"}}
    assert "ui.symbols changed in profile work while it was open" in runtime.model.panes["ui"].error


# --- rankings ---------------------------------------------------------------------

_RANKED = (
    "profiles:\n  default:\n"
    "    shelf: {extensions: {book_source: {rank: {books: [shop, library]}}}}\n"
)


def _shelf() -> Screen[SetupModel, SetupResult]:
    result = shelf.compose(shelf.shelf_spec(), shelf.library_spec(), shelf.shop_spec())
    return setup_screen(
        result,
        setup_services(result, ["shelf"]),
        sections=setup_sections(["shelf"]),
        profile="default",
        store=None,
        profiles=("default",),
    )


def _to_shelf() -> list[ScreenKey]:
    model, _ = _shelf().init()
    index = next(i for i, row in enumerate(model.rows) if row.name == "shelf")
    return [*(["down"] * (index - model.selected)), "enter"]


def _ranks(path: Path) -> object:
    return read_config_dict(path)["profiles"]["default"].get("shelf")


def test_each_ranking_is_a_row_then_one_adds_a_ranking(_isolated_config: Path) -> None:
    write_config(_isolated_config, _RANKED)

    run = drive_screen(_shelf(), _to_shelf(), size=(120, 30))

    assert _rows(run.model, "shelf")[:2] == [
        ("extensions.book_source.rank.books", "shop, library", "profile:default"),
        ("extensions", "+ add a ranking", ""),
    ]


def test_a_ranking_is_reordered_in_the_list_editor(_isolated_config: Path) -> None:
    write_config(_isolated_config, _RANKED)
    keys = [*_to_shelf(), "enter", "down", "m", "up", "enter", "end", "enter"]

    run = drive_screen(_shelf(), keys, size=(120, 30))

    assert _ranks(_isolated_config) == {
        "extensions": {"book_source": {"rank": {"books": ["library", "shop"]}}}
    }
    assert run.model.written == (("default", "shelf.extensions.book_source.rank.books"),)


def test_a_ranking_is_added_as_plugin_rank_takes_it(_isolated_config: Path) -> None:
    write_config(_isolated_config, _RANKED)
    keys = [*_to_shelf(), "down", "enter", Paste("book_source first library"), "enter"]

    run = drive_screen(_shelf(), keys, size=(120, 30))

    assert _ranks(_isolated_config) == {
        "extensions": {
            "book_source": {"rank": {"books": ["shop", "library"], "first": ["library"]}}
        }
    }
    assert run.model.panes["shelf"].rows[1].label == "extensions.book_source.rank.first"


def test_a_refused_ranking_shows_plugin_ranks_message(_isolated_config: Path) -> None:
    write_config(_isolated_config, _RANKED)
    keys = [*_to_shelf(), "down", "enter", Paste("nope books shop"), "enter"]

    run = drive_screen(_shelf(), keys, size=(120, 30))

    assert "shelf declares no contract 'nope'" in run.frame
    assert run.model.panes["shelf"].editing is not None  # still open, to fix


def test_delete_removes_a_ranking(_isolated_config: Path) -> None:
    write_config(_isolated_config, _RANKED)

    drive_screen(_shelf(), [*_to_shelf(), "delete", "y"], size=(120, 30))

    assert _ranks(_isolated_config) is None


def test_a_ranking_a_variable_sets_says_so_and_edits_the_profiles_own(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _RANKED)
    variable = "UNTAPED_SHELF__EXTENSIONS__BOOK_SOURCE__RANK__BOOKS"
    monkeypatch.setenv(variable, '["library"]')

    shown = drive_screen(_shelf(), _to_shelf(), size=(120, 30))
    opened = drive_screen(_shelf(), [*_to_shelf(), "enter"], size=(120, 30))
    drive_screen(_shelf(), [*_to_shelf(), "delete", "y"], size=(120, 30))

    row = shown.model.panes["shelf"].rows[0]
    assert f"{variable} is set: it wins over the profile until it is unset." in row.help
    editing = opened.model.panes["shelf"].editing
    assert isinstance(editing, ListEditor) and editing.value == ("shop", "library")
    assert _ranks(_isolated_config) is None  # delete removed the profile's own ranking


def test_a_ranking_changed_while_its_editor_was_open_is_not_overwritten(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _RANKED)
    host = FakeHost()
    runtime = Runtime(_shelf(), host, theme=BUILTIN_THEMES["default"], size=(120, 30))
    runtime.start()
    for key in [*_to_shelf(), "enter"]:
        runtime.send(_message(key))
    host.run_jobs()
    host.deliver()  # the editor is open, holding [shop, library]

    owners = ("shelf",)
    RankProviders(SettingsFileRepository(), owners)("shelf.book_source", "books", ["library"])
    for key in ["delete", "end", "enter"]:
        runtime.send(_message(key))
    host.run_jobs()
    host.deliver()

    assert _ranks(_isolated_config) == {
        "extensions": {"book_source": {"rank": {"books": ["library"]}}}
    }
    assert "shelf.book_source books changed in profile default while it was open" in (
        runtime.model.panes["shelf"].error
    )


def test_an_untouched_ranking_or_empty_add_writes_nothing(_isolated_config: Path) -> None:
    write_config(_isolated_config, _RANKED)
    before = _isolated_config.read_text(encoding="utf-8")

    ranking = drive_screen(_shelf(), [*_to_shelf(), "enter", "end", "enter"], size=(120, 30))
    add = drive_screen(_shelf(), [*_to_shelf(), "down", "enter", "enter"], size=(120, 30))

    assert _isolated_config.read_text(encoding="utf-8") == before
    assert ranking.model.written == () and add.model.written == ()
    assert "enter add" in drive_screen(_shelf(), [*_to_shelf(), "down"]).frame.splitlines()[-1]


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ("book_source books", "give the contract, the method and its providers"),
        ("book_source 'books shop", "not a command line"),
        ("other.book_source books shop", "rank other.book_source in other's section"),
        ("nowhere books shop", "shelf declares no contract 'nowhere'"),
    ],
)
def test_a_refused_add_says_why(_isolated_config: Path, line: str, error: str) -> None:
    write_config(_isolated_config, _RANKED)
    keys = [*_to_shelf(), "down", "enter", Paste(line), "enter"]

    run = drive_screen(_shelf(), keys, size=(160, 30))

    assert error in run.model.panes["shelf"].editing.error  # type: ignore[union-attr]


def test_an_owner_named_add_and_a_new_profile_rank_as_plugin_rank_does(
    _isolated_config: Path,
) -> None:
    write_config(_isolated_config, _RANKED)
    keys = [*_to_shelf(), "down", "enter", Paste("shelf.book_source first shop"), "enter"]

    run = drive_screen(
        setup_screen(
            shelf.compose(shelf.shelf_spec(), shelf.library_spec(), shelf.shop_spec()),
            {},
            sections=setup_sections(["shelf"]),
            profile="work",
            store=None,
        ),
        keys,
        size=(120, 30),
    )

    assert read_config_dict(_isolated_config)["profiles"]["work"] == {
        "shelf": {"extensions": {"book_source": {"rank": {"first": ["shop"]}}}}
    }
    assert run.model.written == (("work", "shelf.extensions"),)


def test_an_invalid_owner_section_opens_its_extensions_as_json(_isolated_config: Path) -> None:
    write_config(_isolated_config, _RANKED.replace("[shop, library]", "[shop, shop]"))

    run = drive_screen(_shelf(), _to_shelf(), size=(120, 30))

    labels = [row.label for row in run.model.panes["shelf"].rows]
    assert "extensions" in labels and not any(".rank." in label for label in labels)


def test_a_malformed_own_ranking_opens_on_what_the_row_shows(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(_isolated_config, _RANKED.replace("[shop, library]", "shop"))
    monkeypatch.setenv("UNTAPED_SHELF__EXTENSIONS__BOOK_SOURCE__RANK__BOOKS", '["library"]')

    run = drive_screen(_shelf(), [*_to_shelf(), "enter"], size=(120, 30))

    editing = run.model.panes["shelf"].editing
    assert isinstance(editing, ListEditor) and editing.value == ("library",)
