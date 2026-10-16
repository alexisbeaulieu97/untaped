"""A setting's editor, picked from the shape of its field: what the setup screen edits with.

The screen knows shapes, not settings: :func:`editor` reads a field's type
(never its key) and returns the component that edits it, and
:func:`written` turns that component back into the string ``config set``
takes, which the config use cases write. Components add no constraint beyond
their shape (a number field takes only a number); every other check is the
model's, so an edit fails with the same message as ``config set``.

An empty field, or a choice of ``unset``, unsets the key in the profile, so
the row shows what it then inherits. Lists and maps have no editor yet
(:func:`editor` returns ``None``); ``config set`` edits them.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Literal, get_args, get_origin

from pydantic import SecretStr

from untaped.config_schema import FieldDescriptor
from untaped.screen.components.choices import Check, Cycle, ListItem, Select, SingleList
from untaped.screen.components.fields import Field
from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput

__all__ = ["editor", "written"]

type Shape = Literal["check", "cycle", "number", "path", "choice", "secret", "text"]

#: The most choices drawn inline; more open as a ``Select``.
INLINE_CHOICES = 4
#: The choice that unsets an optional choice field.
UNSET = ListItem("", "unset")
EMPTY_UNSETS = "empty unsets it"


def editor(
    descriptor: FieldDescriptor, value: object, *, label: str, raw: bool = False
) -> Field | None:
    """The component that edits ``descriptor``, holding ``value``; ``None`` for a list or map.

    ``raw`` is a value its section failed to validate: it opens as text, as
    ``config set`` takes it, whatever the field's shape.
    """
    shape = "text" if raw and not descriptor.is_collection else _shape(descriptor)
    shown = "" if value is None else str(value)
    match shape:
        case None:
            return None
        case "check":
            return Check(label, value=value is True)
        case "cycle":
            return Cycle(label, (None, True, False), value=value, labels=("unset", "on", "off"))
        case "number":
            integer = descriptor.annotation is int
            return NumberInput(label, text=shown, integer=integer, help=EMPTY_UNSETS)
        case "path":
            return PathInput(label, shown, help=EMPTY_UNSETS)
        case "choice":
            items = tuple(ListItem(name, name) for name in _choices(descriptor))
            if descriptor.optional:
                items = (UNSET, *items)
            if len(items) <= INLINE_CHOICES:
                return SingleList(label, items, value=shown)
            return Select(label, items, value=shown)
        case "secret":
            return SecretInput(label, help=EMPTY_UNSETS)
        case "text":
            return TextInput(label, shown, help=EMPTY_UNSETS)


def written(descriptor: FieldDescriptor, component: Field, *, raw: bool = False) -> str | None:
    """What ``component`` holds as ``config set`` takes it; ``None`` unsets the key."""
    shape = "text" if raw else _shape(descriptor)
    value = component.value
    match shape:
        case "check" | "cycle":
            return None if value is None else str(value).lower()
        case "number":
            assert isinstance(component, NumberInput)
            return component.text.strip() or None
        case "secret":
            assert isinstance(value, SecretStr)
            return value.get_secret_value() or None
    return str(value) or None


def _shape(descriptor: FieldDescriptor) -> Shape | None:
    """The shape of ``descriptor``'s field; ``None`` for a list or map."""
    annotation = descriptor.annotation
    if descriptor.is_collection:
        return None
    if annotation is bool:
        return "cycle" if descriptor.optional else "check"
    if annotation in (int, float):
        return "number"
    if annotation is SecretStr:
        return "secret"
    if isinstance(annotation, type) and issubclass(annotation, PurePath):
        return "path"
    if get_origin(annotation) is Literal or descriptor.choices is not None:
        return "choice"
    return "text"


def _choices(descriptor: FieldDescriptor) -> tuple[str, ...]:
    if descriptor.choices is not None:
        return descriptor.choices.names()
    return tuple(str(arg) for arg in get_args(descriptor.annotation))
