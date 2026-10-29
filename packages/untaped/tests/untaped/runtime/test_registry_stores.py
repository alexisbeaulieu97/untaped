"""``PluginSpec.stores``: what composition accepts and what it quarantines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from untaped.runtime.registry import (
    PluginSpec,
    spliced_state,
    state_model,
)
from untaped.sdk.errors import ConfigError
from untaped.sdk.storage.state import StateMap, StateRows
from untaped.sdk.testing import make_project

pytestmark = pytest.mark.usefixtures("home_run")


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
    "from untaped.runtime.registry import PluginSpec\n"
    "def make(**fields):\n    return PluginSpec(name='hello', **fields)\n",
    _ELSEWHERE,
)


def _hello(**fields: Any) -> PluginSpec:
    spec: PluginSpec = _ELSEWHERE["make"](**fields)
    return spec


ALIASES = StateMap("hello", "aliases")
EVERY_ROW = StateRows.per_project("hello", "here", model=Row, key_field="name")


def test_one_projects_rows_are_never_listed(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p")
    with pytest.raises(ConfigError, match="of one project"):
        _hello(stores=(EVERY_ROW.of(project),))
    with pytest.raises(ConfigError, match="of one project"):
        _hello(stores=(StateMap("hello", "aliases", project=project),))


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
    assert issubclass(spliced, HelloState)
    assert spliced.model_validate({"rows": [{"name": 1}], "cursor": "c"}).model_dump() == {
        "cursor": "c"
    }
    assert spliced_state(spec) is spliced  # registered again as the same section
    assert spliced_state(_hello(state=HelloState)) is HelloState


class StrictState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[Row] = []
    aliases: dict[str, str] = {}
    cursor: str = ""


def test_the_spliced_state_ignores_the_keys_declarations_own() -> None:
    spliced = spliced_state(_hello(state=StrictState, stores=(ROWS, ALIASES)))

    assert spliced is not None
    loaded = spliced.model_validate({"rows": [{"name": 1}], "aliases": {"a": "b"}, "cursor": "c"})
    assert loaded.model_dump() == {"cursor": "c"}


def test_the_state_model_leaves_out_a_projects_rows() -> None:
    model = state_model(_hello(stores=(ROWS, EVERY_ROW)))

    assert model is not None
    assert list(model.model_fields) == ["rows"]


def test_a_strict_state_model_ignores_a_declared_key_it_lacks() -> None:
    class Cursor(BaseModel):
        model_config = ConfigDict(extra="forbid")

        cursor: str = ""

    spliced = spliced_state(_hello(state=Cursor, stores=(ALIASES,)))

    assert spliced is not None
    assert spliced.model_validate({"aliases": {"a": "b"}, "cursor": "c"}).model_dump() == {
        "cursor": "c"
    }


def test_the_state_models_validators_still_run() -> None:
    class Checked(BaseModel):
        cursor: str = ""

        @field_validator("cursor")
        @classmethod
        def _short(cls, value: str) -> str:
            if len(value) > 3:
                raise ValueError("too long")
            return value

    spliced = spliced_state(_hello(state=Checked, stores=(ALIASES,)))

    assert spliced is not None
    with pytest.raises(ValueError, match="too long"):
        spliced.model_validate({"aliases": {}, "cursor": "longer"})


def test_an_after_validator_sees_a_declared_fields_default() -> None:
    class Checked(BaseModel):
        aliases: dict[str, str] = {}
        cursor: str = ""

        @model_validator(mode="after")
        def _has_aliases(self) -> Checked:
            assert isinstance(self.aliases, dict)
            return self

    spliced = spliced_state(_hello(state=Checked, stores=(ALIASES,)))

    assert spliced is not None
    assert spliced.model_validate({"aliases": {"a": "b"}, "cursor": "c"}).model_dump() == {
        "cursor": "c"
    }


def test_the_spliced_state_leaves_extra_unset_unless_it_forbids() -> None:
    spliced = spliced_state(_hello(state=HelloState, stores=(ALIASES,)))

    assert spliced is not None
    assert "extra" not in spliced.model_config


def test_an_open_state_model_stays_open() -> None:
    class Open(BaseModel):
        model_config = ConfigDict(extra="allow")

        cursor: str = ""

    spliced = spliced_state(_hello(state=Open, stores=(ALIASES,)))

    assert spliced is not None
    assert spliced.model_config["extra"] == "allow"
