"""One declared input and how a given value is converted to its type.

:class:`Input` is the union of what the files that declare inputs need; each
consumer subclasses it, narrows ``type`` to its own spellings and adds its own
fields. :func:`coerce` is the one conversion every consumer uses: one bool
vocabulary, a ``list`` from YAML or comma-separated text, a ``dict`` from a
YAML mapping. Its errors never repeat the value, which may be a secret.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from untaped.sdk import experimental
from untaped_values.errors import InputError

ScalarType = Literal["str", "int", "bool", "float"]
"""The element type of a ``list`` input or the value type of a ``dict`` input."""

InputType = Literal["str", "string", "int", "float", "bool", "choice", "list", "dict"]

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@experimental
class Input(BaseModel):
    """One value a file needs from whoever runs it.

    `type` converts a given value: `str` (or `string`), `int`, `float`, `bool`
    (`1/true/yes/on`, `0/false/no/off`), `choice` (one of `choices`), `list`
    (a YAML list, or comma-separated text; `items` is its element type,
    default `str`) or `dict` (a YAML mapping; `values` is its value type,
    default `str`). An input with no `default` must be given, or is asked at a
    terminal. A `secret` input is asked hidden, shown as `***` and never
    rendered into text such as a path or a command line.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field("", description="What the value is; the prompt shows it.")
    type: InputType = Field("str", description="How a given value is converted.")
    default: JsonValue | None = Field(None, description="The value when none is given.")
    secret: bool = Field(False, description="Asked hidden, shown as `***`, never put in text.")
    choices: tuple[JsonValue, ...] = Field((), description="A `choice` input's allowed values.")
    items: ScalarType | None = Field(None, description="A `list` input's element type.")
    values: ScalarType | None = Field(
        None, description="A `dict` input's value type (unrelated to the plugin's name)."
    )

    @model_validator(mode="after")
    def _consistent(self) -> Input:
        if self.items is not None and self.type != "list":
            raise ValueError("items is only valid with type list")
        if self.values is not None and self.type != "dict":
            raise ValueError("values is only valid with type dict")
        if self.choices and self.type != "choice":
            raise ValueError("choices is only valid with type choice")
        if self.type == "choice" and not self.choices:
            raise ValueError("type choice needs a non-empty choices list")
        if self.default is not None:
            try:
                coerce(self, self.default)
            except InputError as exc:
                raise ValueError(f"default: {exc}") from None
        return self


@experimental
def coerce(spec: Input, value: object, *, name: str | None = None) -> object:
    """``value`` converted to ``spec``'s type; :class:`InputError` naming ``name`` when it can't be.

    Already-converted values pass through unchanged, so converting twice is safe.
    """
    try:
        return _coerce(spec, value)
    except InputError as exc:
        if name is None:
            raise
        text = str(exc)
        joiner = " " if text.startswith("expects ") else ": "  # input 'x' expects YAML list
        raise InputError(f"input {name!r}{joiner}{text}") from None


def _coerce(spec: Input, value: object) -> object:
    if spec.type == "list":
        return _coerce_list(_from_text(value, "list") if _yaml_list(value) else value, spec)
    if spec.type == "dict":
        return _coerce_dict(_from_text(value, "dict") if isinstance(value, str) else value, spec)
    if spec.type == "choice":
        for choice in spec.choices:
            if value == choice or str(value) == str(choice):
                return choice
        allowed = ", ".join(repr(choice) for choice in spec.choices)
        raise InputError(f"value is not one of [{allowed}]")
    return _coerce_scalar(value, "str" if spec.type == "string" else spec.type)


def _yaml_list(value: object) -> bool:
    return isinstance(value, str) and value.lstrip().startswith("[")


def _from_text(value: object, kind: Literal["list", "dict"]) -> object:
    expected = "list" if kind == "list" else "mapping"
    try:
        return yaml.safe_load(str(value))
    except yaml.YAMLError as exc:
        raise InputError(f"expects YAML {expected}: {exc}") from None


def _coerce_list(value: object, spec: Input) -> list[object]:
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, Mapping):
        raise InputError("cannot coerce value to list")
    elif not isinstance(value, Sequence) or isinstance(value, bytes):
        value = [value]
    item_type = spec.items or "str"
    try:
        return [_coerce_element(item, item_type) for item in value]
    except InputError:
        raise InputError("cannot coerce value to list") from None


def _coerce_dict(value: object, spec: Input) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise InputError("cannot coerce value to dict")
    value_type = spec.values or "str"
    coerced: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise InputError("dict input keys must be strings")
        try:
            coerced[key] = _coerce_element(item, value_type)
        except InputError:
            raise InputError("cannot coerce value to dict") from None
    return coerced


def _coerce_element(value: object, input_type: ScalarType) -> object:
    if isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, str | bytes)
    ):
        raise InputError(f"cannot coerce value to {input_type}")
    return _coerce_scalar(value, input_type)


def _coerce_scalar(value: object, input_type: str) -> object:
    if input_type == "str":
        return str(value)
    if input_type == "int":
        return _coerce_int(value)
    if input_type == "float":
        return _coerce_float(value)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise InputError("cannot coerce value to bool")


def _coerce_int(value: object) -> int:
    """``value`` as an int; a float truncates."""
    try:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float):
            return int(value)
        return int(str(value))
    except TypeError, ValueError, OverflowError:
        raise InputError("cannot coerce value to int") from None


def _coerce_float(value: object) -> float:
    """``value`` as a float."""
    try:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return float(str(value))
    except TypeError, ValueError, OverflowError:
        raise InputError("cannot coerce value to float") from None
