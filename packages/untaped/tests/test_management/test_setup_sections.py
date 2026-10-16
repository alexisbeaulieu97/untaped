"""The setup screen's sections: rows, sources, help, and editing a setting from its row.

Every screen case drives the real screen (``drive_screen``, commands run
synchronously) over the isolated config, with ``knobs``, a plugin double whose
settings cover each shape. The mapping cases build each shape's field directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, ClassVar, Literal

import pytest
from pydantic import BaseModel, Field, SecretStr

from screen.support import FakeHost
from test_management.support import WizProfile, compose, make_spec, write_config
from untaped.config.fields import editor, written
from untaped.config.repository import SettingsFileRepository
from untaped.config.use_cases import SetSetting
from untaped.config_file import read_config_dict
from untaped.config_schema import Argv, Choices, FieldDescriptor, walk_settings
from untaped.deprecated_keys import Renamed
from untaped.management.setup_screen import SetupModel, SetupResult, setup_screen
from untaped.management.setup_state import setup_sections, setup_services
from untaped.plugins.registry import PluginSpec
from untaped.screen.components.choices import Check, Cycle, Select, SingleList
from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput
from untaped.screen.components.lists import ListEditor
from untaped.screen.core import Screen
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
