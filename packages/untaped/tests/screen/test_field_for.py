"""``field_for``: a setting's type to the component that edits it."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

import pytest
from annotated_types import Ge, Gt, Interval, Le, Lt
from pydantic import BaseModel, Field, SecretStr

from screen.gallery import field_of, run_solo
from untaped.config_schema import FieldDescriptor, find_descriptor, walk_settings
from untaped.screen.components.choices import Check, Select, SingleList
from untaped.screen.components.fields import Field as FieldProtocol
from untaped.screen.components.fields import field_for
from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput
from untaped.settings import Settings
from untaped.stability import Experimental, function_mark


def _descriptor(
    annotation: Any, *, default: Any = None, has_default: bool = True
) -> FieldDescriptor:
    return FieldDescriptor(
        path=("section", "key"),
        annotation=annotation,
        default=default,
        has_default=has_default,
        is_secret=annotation is SecretStr,
    )


@pytest.mark.parametrize(
    ("annotation", "component"),
    [
        (bool, Check),
        (int, NumberInput),
        (float, NumberInput),
        (str, TextInput),
        (Path, PathInput),
        (PurePosixPath, PathInput),
        (SecretStr, SecretInput),
        (Literal["a", "b"], SingleList),
        (Literal["a", "b", "c", "d"], SingleList),
        (Literal["a", "b", "c", "d", "e"], Select),
    ],
)
def test_each_annotation_maps_to_its_component(annotation: Any, component: type) -> None:
    built = field_for(_descriptor(annotation))

    assert type(built) is component
    assert built.label == "section.key"  # type: ignore[attr-defined]


def test_a_bool_is_a_check_not_a_number() -> None:
    assert isinstance(field_for(_descriptor(bool, default=True)), Check)
    assert field_for(_descriptor(bool, default=True)).value is True
    assert field_for(_descriptor(bool, default=False), value=True).value is True
    assert field_for(_descriptor(bool, has_default=False)).value is False


@pytest.mark.parametrize("annotation", [list[str], dict[str, str], bytes, object, tuple[int, int]])
def test_an_unmapped_annotation_raises_and_names_the_key(annotation: Any) -> None:
    with pytest.raises(TypeError, match=r"section\.key"):
        field_for(_descriptor(annotation))


def test_a_literal_of_non_strings_is_unmapped() -> None:
    with pytest.raises(TypeError, match="only Literal strings"):
        field_for(_descriptor(Literal[1, 2]))


def test_secretstr_maps_to_a_secret_input_holding_a_secretstr() -> None:
    built = field_for(_descriptor(SecretStr), value="hunter2")

    assert isinstance(built, SecretInput)
    assert isinstance(built.value, SecretStr)
    assert built.value.get_secret_value() == "hunter2"
    assert "hunter2" not in repr(built)
    assert field_for(_descriptor(SecretStr, default=SecretStr("kept"))).value == SecretStr("kept")
    assert field_for(_descriptor(SecretStr)).value == SecretStr("")


def test_the_starting_value_is_the_argument_else_the_default_else_empty() -> None:
    assert field_for(_descriptor(str, default="dflt")).value == "dflt"
    assert field_for(_descriptor(str, default="dflt"), value="given").value == "given"
    assert field_for(_descriptor(str)).value == ""
    assert field_for(_descriptor(str, has_default=False)).value == ""
    assert field_for(_descriptor(Path, default=Path("/tmp/x"))).value == "/tmp/x"
    assert field_for(_descriptor(int, default=30)).text == "30"  # type: ignore[attr-defined]
    assert field_for(_descriptor(float, default=1800.0)).text == "1800"  # type: ignore[attr-defined]
    assert field_for(_descriptor(float, default=0.5)).text == "0.5"  # type: ignore[attr-defined]
    assert field_for(_descriptor(int)).text == ""  # type: ignore[attr-defined]


def test_a_literal_starts_on_its_default_and_ignores_a_value_it_does_not_list() -> None:
    annotation = Literal["rounded", "square", "ascii", "none"]
    assert field_for(_descriptor(annotation, default="ascii")).value == "ascii"
    assert field_for(_descriptor(annotation, default="ascii"), value="none").value == "none"
    assert field_for(_descriptor(annotation, default="nope")).value == ""


def test_help_is_a_parameter_because_a_descriptor_has_no_description() -> None:
    built = field_for(_descriptor(str), help="The controller address.")

    assert "The controller address." in run_solo(built).frame


@pytest.mark.parametrize(
    ("metadata", "minimum", "maximum"),
    [
        (Ge(1), 1, None),
        (Le(600), None, 600),
        (Interval(ge=1, le=600), 1, 600),
        (Gt(0), 1, None),  # an integer's exclusive bound is the next integer
        (Lt(10), None, 9),
        (Field(ge=2, le=9), 2, 9),
    ],
)
def test_number_bounds_come_from_annotated_metadata(
    metadata: object, minimum: float | None, maximum: float | None
) -> None:
    built = field_for(_descriptor(Annotated[int, metadata]))

    assert isinstance(built, NumberInput)
    assert (built.minimum, built.maximum) == (minimum, maximum)


def test_a_floats_exclusive_bound_is_left_to_the_settings_own_validation() -> None:
    built = field_for(_descriptor(Annotated[float, Gt(0)]))

    assert isinstance(built, NumberInput)
    assert (built.minimum, built.maximum, built.integer) == (None, None, False)
    both = field_for(_descriptor(Annotated[float, Ge(0.5), Le(2.5)]))
    assert (both.minimum, both.maximum) == (0.5, 2.5)  # type: ignore[attr-defined]


def test_without_annotated_metadata_a_number_has_no_bounds_and_its_validation_is_the_types() -> (
    None
):
    built = field_for(_descriptor(int, default=3))

    assert isinstance(built, NumberInput)
    assert (built.minimum, built.maximum) == (None, None)
    assert built.validate() == ""
    assert field_for(_descriptor(int), value="x").validate() == "Must be a whole number."
    assert field_for(_descriptor(int), value="30").value == 30


def test_a_real_descriptor_maps_to_its_component() -> None:
    descriptors = walk_settings(Settings)

    verify = find_descriptor(descriptors, "http.verify_ssl")
    assert verify is not None
    assert isinstance(field_for(verify), Check)
    assert field_for(verify).value is True

    border = find_descriptor(descriptors, "ui.border")
    assert border is not None
    assert isinstance(field_for(border), SingleList)  # four choices

    output = find_descriptor(descriptors, "ui.format")
    assert output is not None
    assert isinstance(field_for(output), Select)  # five choices
    assert {item.id for item in field_for(output).choices} == {  # type: ignore[attr-defined]
        "json", "yaml", "table", "raw", "pipe"
    }  # fmt: skip


def test_every_core_setting_has_a_component_and_each_renders() -> None:
    for descriptor in walk_settings(Settings):
        built = field_for(descriptor)
        assert run_solo(built).frame.strip(), descriptor.key


def test_a_pydantic_models_constraints_reach_the_component_through_annotated() -> None:
    class Section(BaseModel):
        parallel: Annotated[int, Field(ge=1, le=32)] = 8
        token: SecretStr | None = None
        mode: Literal["a", "b"] = "a"

    descriptors = walk_settings(Section)
    parallel = field_for(descriptors[0])

    assert type(field_for(descriptors[1])) is SecretInput
    assert type(field_for(descriptors[2])) is SingleList
    assert isinstance(parallel, NumberInput)
    assert parallel.text == "8"


def test_a_field_built_by_field_for_edits_through_the_runtime() -> None:
    check = run_solo(field_for(_descriptor(bool, default=False)), " ")
    assert field_of(check).value is True
    text = run_solo(field_for(_descriptor(str)), "h", "i")
    assert field_of(text).value == "hi"
    number = run_solo(field_for(_descriptor(int, default=3)), "backspace", "4", "2")
    assert field_of(number).value == 42
    path = run_solo(field_for(_descriptor(Path)), "/")
    assert field_of(path).value == "/"


def test_every_component_satisfies_the_field_contract_and_field_for_is_experimental() -> None:

    built: list[FieldProtocol] = [
        field_for(_descriptor(annotation))
        for annotation in (
            bool,
            int,
            str,
            Path,
            SecretStr,
            Literal["a"],
            Literal["a", "b", "c", "d", "e"],
        )
    ]
    for component in built:
        assert {"value", "error", "update", "view", "with_error", "validate"} <= set(dir(component))
        assert component.error == ""
        assert component.with_error("Nope.").error == "Nope."
        assert component.validate() == ""
        assert {f.name for f in fields(component)} >= {"label"}  # type: ignore[arg-type]
    assert isinstance(function_mark(field_for), Experimental)
    assert isinstance(function_mark(FieldProtocol), Experimental)
