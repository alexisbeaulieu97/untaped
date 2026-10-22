"""``PluginSpec.stores``: what composition accepts and what it quarantines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from test_plugins.plugin_harness import make_candidate, make_shell
from untaped.errors import ConfigError
from untaped.plugins.registry import (
    PluginSpec,
    compose,
    spliced_state,
    state_declarations,
    state_keys,
    state_model,
)
from untaped.state import StateCollection, StateMap, StateRows
from untaped.store import Store
from untaped.testing import make_project

DATA = Store("hello", "data", base=lambda: Path("data.db"))


def _reason(spec: PluginSpec) -> tuple[str, str]:
    (record,) = compose(make_shell(), [make_candidate(spec)]).quarantine
    return record.reason, record.detail


def test_a_plugin_composes_its_stores() -> None:
    result = compose(make_shell(), [make_candidate(_hello(stores=[DATA]))])
    assert result.quarantine == ()
    assert result.plugins[0].spec.stores == (DATA,)


def test_a_store_of_another_plugin_is_quarantined() -> None:
    other = Store("other", "data", base=lambda: Path("x.db"))
    reason, detail = _reason(PluginSpec(name="hello", stores=(other,)))
    assert reason == "foreign-store"
    assert "'other.data'" in detail


def test_a_store_declared_twice_is_quarantined() -> None:
    again = Store("hello", "data", base=lambda: Path("y.db"), layout=2)
    reason, detail = _reason(PluginSpec(name="hello", stores=(DATA, again)))
    assert reason == "duplicate-store"
    assert "'data' twice" in detail


def test_stores_holds_only_stores() -> None:
    with pytest.raises(ConfigError, match="not a Store"):
        PluginSpec(name="hello", stores=("data",))  # type: ignore[arg-type]


# -- state declarations ----------------------------------------------------------


class Row(BaseModel):
    name: str
    note: str = ""


ROWS = StateRows("hello", "rows", model=Row, key_field="name")
# Specs below are made as another package's would be, so this module's rows aren't theirs to list.
_ELSEWHERE: dict[str, Any] = {"__name__": "untaped_elsewhere"}
exec(
    "from untaped.plugins.registry import PluginSpec\n"
    "def make(**fields):\n    return PluginSpec(name='hello', **fields)\n",
    _ELSEWHERE,
)


def _hello(**fields: Any) -> PluginSpec:
    spec: PluginSpec = _ELSEWHERE["make"](**fields)
    return spec


ALIASES = StateMap("hello", "aliases")
EVERY_ROW = StateRows.per_project("hello", "here", model=Row, key_field="name")


def test_state_declarations_compose_and_name_the_state_keys() -> None:
    spec = _hello(stores=(DATA, ROWS, ALIASES, EVERY_ROW))

    result = compose(make_shell(), [make_candidate(spec)])

    assert result.quarantine == ()
    assert state_declarations(spec) == (ROWS, ALIASES, EVERY_ROW)
    assert state_keys(spec) == {"rows", "aliases", "here"}


def test_state_and_files_are_named_apart() -> None:
    same_name = StateRows("hello", "data", model=Row, key_field="name")
    spec = _hello(stores=(DATA, same_name, ROWS))
    assert compose(make_shell(), [make_candidate(spec)]).quarantine == ()


def test_state_of_another_plugin_or_named_twice_is_quarantined() -> None:
    other = StateMap("other", "aliases")
    assert _reason(_hello(stores=(ROWS, other))) == (
        "foreign-store",
        "plugin 'hello' declares the state 'other.aliases' of another plugin",
    )
    again = StateCollection("hello", "rows")
    assert _reason(_hello(stores=(ROWS, again))) == (
        "duplicate-store",
        "plugin 'hello' declares the state 'rows' twice",
    )


def test_rows_its_package_declares_but_doesnt_list_are_quarantined() -> None:
    unlisted = StateRows("hello", "unlisted", model=Row, key_field="name")

    reason, detail = _reason(PluginSpec(name="hello", stores=(ROWS, EVERY_ROW)))

    assert reason == "unlisted-state"
    assert "'hello.unlisted'" in detail
    del unlisted


def test_rows_another_module_declares_are_not_this_plugins() -> None:
    assert compose(make_shell(), [make_candidate(_hello())]).quarantine == ()


def test_one_projects_rows_are_never_listed(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p")
    with pytest.raises(ConfigError, match="of one project"):
        _hello(stores=(EVERY_ROW.of(project),))
    with pytest.raises(ConfigError, match="of one project"):
        _hello(stores=(StateMap("hello", "aliases", project=project),))


def test_a_state_key_a_setting_takes_is_quarantined() -> None:
    class Settings(BaseModel):
        rows: int = 0

    reason, detail = _reason(_hello(settings=Settings, stores=(ROWS,)))
    assert reason == "settings-state-overlap"
    assert "rows" in detail


def test_a_state_key_untaped_reserves_is_quarantined() -> None:
    caches = StateMap("hello", "caches")
    reason, detail = _reason(_hello(stores=(caches,)))
    assert reason == "bad-settings-keys"
    assert "caches" in detail


class HelloState(BaseModel):
    rows: list[Row] = []
    cursor: str = ""


def test_the_state_model_adds_a_field_per_declaration_the_state_model_lacks() -> None:
    model = state_model(_hello(state=HelloState, stores=(ROWS, ALIASES)))

    assert model is not None
    assert list(model.model_fields) == ["rows", "cursor", "aliases"]
    assert model.model_fields["rows"].annotation == list[Row]
    assert model.model_fields["aliases"].annotation == dict[str, str]
    assert state_model(_hello(state=HelloState)) is HelloState
    assert state_model(_hello()) is None


def test_the_spliced_state_leaves_out_what_rows_own() -> None:
    spec = _hello(state=HelloState, stores=(ROWS,))

    spliced = spliced_state(spec)

    assert spliced is not None
    assert list(spliced.model_fields) == ["cursor"]
    assert spliced_state(spec) is spliced  # registered again as the same section
    assert spliced_state(_hello(state=HelloState)) is HelloState
