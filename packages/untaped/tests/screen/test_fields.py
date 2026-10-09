"""The ``Field`` contract: every editing component offers it to the parent that holds it."""

from __future__ import annotations

from dataclasses import fields

from pydantic import SecretStr

from untaped.screen.components.choices import (
    Check,
    Cycle,
    ListItem,
    MultiList,
    Select,
    SingleList,
)
from untaped.screen.components.fields import Field
from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput
from untaped.stability import Experimental, function_mark

ITEMS = tuple(ListItem(name, name) for name in "abcde")


def test_every_component_satisfies_the_field_contract_and_field_is_experimental() -> None:
    built: list[Field] = [
        Check("check"),
        Cycle("cycle", (None, True, False), None, labels=("unset", "on", "off")),
        NumberInput("number", text="3"),
        TextInput("text"),
        PathInput("path"),
        SecretInput("secret", SecretStr("")),
        SingleList("list", ITEMS[:1]),
        MultiList("many", ITEMS),
        Select("select", ITEMS),
    ]
    for component in built:
        assert {"value", "error", "update", "view", "with_error", "validate"} <= set(dir(component))
        assert component.error == ""
        assert component.with_error("Nope.").error == "Nope."
        assert component.validate() == ""
        assert {f.name for f in fields(component)} >= {"label"}  # type: ignore[arg-type]
    assert isinstance(function_mark(Field), Experimental)
