"""Stability marks on settings fields: where they may sit, and how a misplaced one is refused."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from test_plugins.plugin_harness import make_spec
from untaped import bootstrap
from untaped.conventions.settings_names import settings_name_violations
from untaped.deprecated_keys import key_mappings, mapping_errors
from untaped.errors import ConfigError
from untaped.plugins.registry import PluginSpec
from untaped.stability import (
    Deprecated,
    deprecated,
    experimental,
    field_marks,
    mark_errors,
    pydantic_deprecated_fields,
)
from untaped.testing import plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")


class Sweep(BaseModel):
    parallel: Annotated[int, experimental, Field(gt=0)] = 4
    old: Annotated[bool, deprecated(replacement="sweep.parallel")] = False


class Section(BaseModel):
    plain: int = 1
    optional: Annotated[int | None, experimental] = None
    legacy: Annotated[bool, deprecated(replacement="a shell alias")] = False
    sweep: Sweep = Field(default_factory=Sweep)


def test_leaf_marks_are_collected_with_dotted_paths() -> None:
    assert field_marks(Section) == {
        "optional": experimental,
        "legacy": Deprecated("a shell alias"),
        "sweep.parallel": experimental,
        "sweep.old": Deprecated("sweep.parallel"),
    }
    assert mark_errors(Section) == []


def test_a_deprecated_field_feeds_the_key_mappings_with_its_replacement() -> None:
    assert key_mappings(Section).deprecated == {
        "legacy": "a shell alias",
        "sweep.old": "sweep.parallel",
    }


def test_a_mark_without_a_replacement_maps_to_none() -> None:
    class Bare(BaseModel):
        old: Annotated[int, deprecated()] = 0

    assert key_mappings(Bare).deprecated == {"old": None}


def test_reading_a_marked_attribute_emits_no_python_deprecation_warning() -> None:
    section = Section()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert section.legacy is False
        assert section.sweep.old is False
        assert Section.model_json_schema()["properties"]["legacy"]["type"] == "boolean"


class UnionMark(BaseModel):
    value: Annotated[int, experimental] | None = None


class NestedMark(BaseModel):
    values: list[Annotated[int, deprecated()]] = Field(default_factory=list)


class Inner(BaseModel):
    flag: bool = False


class ModelMark(BaseModel):
    inner: Annotated[Inner, experimental] = Field(default_factory=Inner)


class PythonDeprecated(BaseModel):
    old: Annotated[int, warnings.deprecated("old")] = 0


class TwoMarks(BaseModel):
    value: Annotated[int, experimental, deprecated()] = 0


class ObjectReplacement(BaseModel):
    value: Annotated[int, deprecated(replacement=print)] = 0


@pytest.mark.parametrize(
    ("model", "error"),
    [
        (UnionMark, "sits inside its type"),
        (NestedMark, "sits inside its type"),
        (ModelMark, "'inner' is a model; mark its fields instead"),
        (TwoMarks, "more than one stability mark"),
        (ObjectReplacement, "replacement of the mark on setting 'value' must be text"),
    ],
)
def test_a_misplaced_mark_is_refused_loudly(model: type[BaseModel], error: str) -> None:
    assert any(error in sentence for sentence in mark_errors(model)), mark_errors(model)
    assert any(error in sentence for sentence in mapping_errors(model))
    with pytest.raises(ConfigError, match="invalid key declarations"):
        key_mappings(model)


def test_a_pydantic_deprecated_field_still_loads_but_is_a_lint_error() -> None:
    assert mark_errors(PythonDeprecated) == []
    assert pydantic_deprecated_fields(PythonDeprecated) == ["old"]

    composition = bootstrap.compose_root(
        candidates=[plugin_candidate(make_spec(name="svc", settings=PythonDeprecated))]
    )

    assert [plugin.spec.name for plugin in composition.plugins] == ["svc"]


def test_a_misplaced_mark_quarantines_its_provider() -> None:
    spec = make_spec(name="svc", settings=UnionMark)

    composition = bootstrap.compose_root(candidates=[plugin_candidate(spec)])

    assert composition.plugins == ()
    [record] = composition.quarantine
    assert record.reason == "bad-settings-keys"
    assert "sits inside its type" in record.detail


class MarkedState(BaseModel):
    last_run: Annotated[str, experimental] = ""


def _state_spec(state: type[BaseModel]) -> PluginSpec:
    return make_spec(name="svc", settings=Section, state=state)


def test_a_mark_on_a_state_field_is_refused_and_quarantines_its_provider() -> None:
    assert mark_errors(MarkedState, state=True) == [
        "setting 'last_run' is state: state fields take no stability marks"
    ]
    assert mark_errors(MarkedState) == []

    composition = bootstrap.compose_root(candidates=[plugin_candidate(_state_spec(MarkedState))])

    [record] = composition.quarantine
    assert (record.reason, "state fields take no stability marks" in record.detail) == (
        "bad-settings-keys",
        True,
    )


def test_the_lint_reports_a_misplaced_mark_with_its_file_and_line() -> None:
    root = Path(__file__).resolve().parents[3]

    [line] = settings_name_violations("svc", UnionMark, root)

    location, rule, detail = line.split("::")
    assert location.startswith("packages/untaped/tests/test_stability_settings.py:")
    assert location.rsplit(":", 1)[1].isdigit()
    assert (rule, "sits inside its type" in detail) == ("settings-renames", True)


def test_the_lint_checks_a_state_model_for_marks() -> None:
    root = Path(__file__).resolve().parents[3]

    [line] = settings_name_violations("svc", Section, root, state=MarkedState)

    assert "::settings-renames::setting 'last_run' is state" in line


def test_a_clean_marked_section_composes() -> None:
    composition = bootstrap.compose_root(
        candidates=[plugin_candidate(make_spec(name="svc", settings=Section))]
    )

    assert [plugin.spec.name for plugin in composition.plugins] == ["svc"]
