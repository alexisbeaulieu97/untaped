"""An input's declaration and how ``coerce`` converts a given value to its type."""

from __future__ import annotations

import traceback

import pytest
from pydantic import ValidationError

from untaped_values.api import Input, InputError, coerce

_INT_LIST = {"type": "list", "items": "int"}
_INT_DICT = {"type": "dict", "values": "int"}


@pytest.mark.parametrize(
    ("spec", "value", "expected"),
    [
        ({"type": "str"}, "api", "api"),
        ({"type": "string"}, 3, "3"),
        ({"type": "int"}, "3", 3),
        ({"type": "int"}, 3.9, 3),
        ({"type": "float"}, "2.25", 2.25),
        *(({"type": "bool"}, word, True) for word in ("1", "true", "Yes", " on ")),
        *(({"type": "bool"}, word, False) for word in ("0", "false", "NO", "off")),
        ({"type": "bool"}, False, False),
        (_INT_LIST, ["1", 2], [1, 2]),
        (_INT_LIST, (), []),
        (
            {"type": "dict", "values": "bool"},
            {"on": "true", "off": False},
            {"on": True, "off": False},
        ),
        ({"type": "dict", "values": "bool"}, {}, {}),
        # Without items or values, elements stay as given.
        ({"type": "list"}, [1, "api"], [1, "api"]),
        ({"type": "list"}, [{"host": "a"}], [{"host": "a"}]),
        ({"type": "list"}, "[1, b]", [1, "b"]),
        ({"type": "dict"}, {"replicas": 3}, {"replicas": 3}),
        ({"type": "dict"}, "{a: [1]}", {"a": [1]}),
        ({"type": "list", "items": "str"}, [1, "api"], ["1", "api"]),
        ({"type": "dict", "values": "str"}, {"replicas": 3}, {"replicas": "3"}),
        # A list from text: YAML when it starts with "[", else comma-separated.
        ({"type": "list"}, "[a, b]", ["a", "b"]),
        ({"type": "list"}, "a, b,,c", ["a", "b", "c"]),
        ({"type": "list"}, "enabled", ["enabled"]),
        (_INT_LIST, "1,2", [1, 2]),
        ({"type": "dict", "values": "int"}, "{replicas: 3}", {"replicas": 3}),
        ({"type": "choice", "choices": ["dev", "prod"]}, "prod", "prod"),
        ({"type": "choice", "choices": [1, 2]}, "2", 2),
    ],
)
def test_coerce_converts_to_the_declared_type(
    spec: dict[str, object], value: object, expected: object
) -> None:
    assert coerce(Input.model_validate(spec), value) == expected


def test_coerce_is_safe_to_apply_twice() -> None:
    spec = Input.model_validate(_INT_LIST)
    assert coerce(spec, coerce(spec, "1, 2")) == [1, 2]


@pytest.mark.parametrize(
    ("spec", "value", "match"),
    [
        (_INT_LIST, "not-a-list", "cannot coerce value to list"),
        (_INT_LIST, ["not-an-int"], "cannot coerce value to list"),
        (_INT_LIST, [["nested"]], "cannot coerce value to list"),
        (_INT_LIST, {"a": 1}, "cannot coerce value to list"),
        ({"type": "list"}, 5, "cannot coerce value to list"),
        ({"type": "list"}, None, "cannot coerce value to list"),
        (_INT_LIST, "[", "expects YAML list:"),
        (_INT_DICT, "not-a-dict", "cannot coerce value to dict"),
        (_INT_DICT, {1: "2"}, "dict input keys must be strings"),
        (_INT_DICT, {"replicas": "not-an-int"}, "cannot coerce value to dict"),
        (_INT_DICT, "{", "expects YAML mapping:"),
        ({"type": "choice", "choices": ["dev"]}, "prod", r"value is not one of \['dev'\]"),
    ],
)
def test_coerce_errors_use_pinned_messages(
    spec: dict[str, object], value: object, match: str
) -> None:
    with pytest.raises(InputError, match=match):
        coerce(Input.model_validate(spec), value)


def test_coerce_names_the_input_when_given_its_name() -> None:
    with pytest.raises(InputError, match=r"^input 'replicas': cannot coerce value to int$"):
        coerce(Input(type="int"), "x", name="replicas")
    with pytest.raises(InputError, match=r"^input 'cols' expects YAML list: "):
        coerce(Input(type="list"), "[", name="cols")


@pytest.mark.parametrize("input_type", ["int", "float", "bool"])
def test_coercion_errors_do_not_echo_values(input_type: str) -> None:
    secret = "TOP-SECRET-9000"

    with pytest.raises(InputError, match=f"cannot coerce value to {input_type}") as excinfo:
        coerce(Input(type=input_type), secret, name="token")  # type: ignore[arg-type]

    assert secret not in str(excinfo.value)
    assert secret not in "".join(
        traceback.format_exception(type(excinfo.value), excinfo.value, excinfo.value.__traceback__)
    )


@pytest.mark.parametrize(
    ("input_type", "text"),
    [("list", "[TOP-SECRET-9000, "), ("dict", "{TOP-SECRET-9000: [")],
)
def test_yaml_errors_do_not_echo_values(input_type: str, text: str) -> None:
    secret = "TOP-SECRET-9000"

    with pytest.raises(InputError, match="the text is not valid YAML") as excinfo:
        coerce(Input(type=input_type, secret=True), text, name="token")  # type: ignore[arg-type]

    assert secret not in str(excinfo.value)
    assert secret not in "".join(
        traceback.format_exception(type(excinfo.value), excinfo.value, excinfo.value.__traceback__)
    )


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ({"type": "str", "items": "int"}, "items is only valid with type list"),
        ({"type": "str", "values": "int"}, "values is only valid with type dict"),
        ({"type": "str", "choices": ["a"]}, "choices is only valid with type choice"),
        ({"type": "choice"}, "type choice needs a non-empty choices list"),
        ({"type": "int", "default": "x"}, "default: cannot coerce value to int"),
        ({"type": "choice", "choices": ["a"], "default": "b"}, "default: value is not one of"),
        ({"type": "str", "sensitive": True}, "sensitive"),
    ],
)
def test_input_refuses_an_inconsistent_declaration(spec: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        Input.model_validate(spec)
