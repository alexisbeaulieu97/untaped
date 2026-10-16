"""A setting's editor, picked from the shape of its field: what the setup screen edits with.

The screen knows shapes, not settings: :func:`editor` reads a field's type
(never its key) and returns the component that edits it, and
:func:`written` turns that component back into the string ``config set``
takes, which the config use cases write. Components add no constraint beyond
their shape (a number field takes only a number); every other check is the
model's, so an edit fails with the same message as ``config set``.

An empty field, or a choice of ``unset``, unsets the key in the profile, so
the row shows what it then inherits; a secret is the exception: it opens
empty, so empty keeps it (``delete`` on its row unsets it). A list of
strings opens the list editor and is written whole, an empty list included
(it overrides an inherited one; ``delete`` unsets). A command's argv
(:class:`~untaped.config_schema.Argv`) is one shell-quoted line. A map whose
:class:`~untaped.config_schema.Choices` declares its names opens on those
names (:func:`opened_value` reads them), each set or cleared in place, and
its row counts the profile's own (:func:`summary`). Any other list or map is
one line of JSON, as ``config set`` takes it.
"""

from __future__ import annotations

import json
import shlex
from pathlib import PurePath
from typing import Literal, get_args, get_origin

from pydantic import SecretStr
from pydantic_core import to_jsonable_python

from untaped.config.ports import SettingsReader
from untaped.config.use_cases import GetSetting, ListNames, NamedValue
from untaped.config_schema import FieldDescriptor
from untaped.screen.components.choices import Check, Cycle, ListItem, Select, SingleList
from untaped.screen.components.fields import Field
from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput
from untaped.screen.components.lists import ListEditor, MapEditor, MapEntry

__all__ = ["editor", "opened_value", "summary", "written"]

type Shape = Literal[
    "check", "cycle", "number", "path", "choice", "secret", "text", "list", "argv", "names", "json"
]

#: The most choices drawn inline; more open as a ``Select``.
INLINE_CHOICES = 4
#: The choice that unsets an optional choice field.
UNSET = ListItem("", "unset")
EMPTY_UNSETS = "empty unsets it"
#: A secret opens empty, so empty leaves it as it is; ``delete`` on its row unsets it.
EMPTY_KEEPS = "empty keeps it; delete on its row unsets it"
LIST_HELP = "enter edit · delete remove · m move · done or ctrl-s writes"
ARGV_HELP = "the command as you would type it; empty unsets it"
JSON_HELP = "JSON, as config set takes it; empty unsets it"
NAMES_HELP = "enter edit · delete clears · done or ctrl-s writes"


def opened_value(repo: SettingsReader, descriptor: FieldDescriptor, *, profile: str) -> object:
    """What ``descriptor``'s editor opens on, read through ``repo`` as ``profile`` sees it.

    A map that declares its names opens on them, each showing what it is
    when ``profile`` doesn't set it; a secret opens empty; any other field on
    its effective value.
    """
    if _shape(descriptor) == "names":
        return ListNames(repo)(descriptor.key, under=profile)
    if descriptor.is_secret:
        return None
    return GetSetting(repo)(descriptor.key, reveal_secrets=True).value


def summary(descriptor: FieldDescriptor, own: object) -> str | None:
    """How a row shows ``descriptor`` when its value doesn't fit one line: a map of names
    counts what the profile sets itself (``own``); ``None`` for every other shape."""
    if _shape(descriptor) != "names":
        return None
    count = len(own) if isinstance(own, dict) else 0
    return f"{count} override{'' if count == 1 else 's'}" if count else "no overrides"


def editor(
    descriptor: FieldDescriptor,
    value: object,
    *,
    label: str,
    raw: bool = False,
    own: object = None,
    own_source: str = "",
) -> Field:
    """The component that edits ``descriptor``, holding ``value``.

    ``raw`` is a value its section failed to validate (as the row shows it):
    it opens as text, or as JSON for a list or map, as ``config set`` takes
    it, whatever the field's shape. A map that declares its names opens on
    them (``value`` is what ``ListNames`` gives), editing ``own``, what the
    profile itself sets; a name set there shows ``own_source``.
    """
    shape = _raw_shape(descriptor) if raw else _shape(descriptor)
    if shape == "names":
        return _names(value, own, label=label, own_source=own_source)
    if shape in ("list", "argv", "json"):
        return _collection(shape, value, label=label, raw=raw)
    shown = "" if value is None else str(value)
    match shape:
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
            return SecretInput(label, help=EMPTY_KEEPS)
    return TextInput(label, shown, help=EMPTY_UNSETS)


def written(descriptor: FieldDescriptor, component: Field, *, raw: bool = False) -> str | None:
    """What ``component`` holds as ``config set`` takes it; ``None`` unsets the key."""
    shape = _raw_shape(descriptor) if raw else _shape(descriptor)
    value = component.value
    match shape:
        case "list":
            assert isinstance(value, tuple)
            return json.dumps(list(value))
        case "names":
            assert isinstance(value, dict)
            return json.dumps(value) if value else None
        case "argv":
            return json.dumps(shlex.split(str(value))) if str(value).strip() else None
        case "check" | "cycle":
            return None if value is None else str(value).lower()
        case "number":
            assert isinstance(component, NumberInput)
            return component.text.strip() or None
        case "secret":
            assert isinstance(value, SecretStr)
            return value.get_secret_value() or None
    return str(value) or None


def _shape(descriptor: FieldDescriptor) -> Shape:
    """The shape of ``descriptor``'s field."""
    annotation = descriptor.annotation
    if descriptor.argv:
        return "argv"
    if get_origin(annotation) is list and get_args(annotation) == (str,):
        return "list"
    if descriptor.is_collection:
        return (
            "names" if descriptor.choices is not None and get_origin(annotation) is dict else "json"
        )
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


def _collection(shape: Shape, value: object, *, label: str, raw: bool) -> Field:
    """The editor of a list or map: the list editor, a command line, or one line of JSON."""
    strings = [str(item) for item in value] if isinstance(value, list | tuple) else []
    if shape == "list":
        return ListEditor(label, tuple(strings), help=LIST_HELP)
    if shape == "argv":
        return TextInput(label, shlex.join(strings), help=ARGV_HELP, validator=_argv_error)
    text = "" if value is None else str(value) if raw else _json(value)
    return TextInput(label, text, help=JSON_HELP)


def _names(value: object, own: object, *, label: str, own_source: str) -> Field:
    """The editor of a map that declares its names: a row per name, editing what ``own`` sets."""
    names = value if isinstance(value, tuple) else ()
    entries = tuple(
        MapEntry(item.name, item.value, item.source, item.declared, item.variable or "")
        for item in names
        if isinstance(item, NamedValue)
    )
    mine = own if isinstance(own, dict) else {}
    ordered = tuple((entry.name, str(mine[entry.name])) for entry in entries if entry.name in mine)
    return MapEditor(label, entries, ordered, own_source=own_source, help=NAMES_HELP)


def _raw_shape(descriptor: FieldDescriptor) -> Shape:
    """The shape a value its section failed to validate opens as: text, or JSON for a collection."""
    return "json" if descriptor.is_collection else "text"


def _json(value: object) -> str:
    return json.dumps(to_jsonable_python(value), ensure_ascii=False)


def _argv_error(text: str) -> str:
    try:
        shlex.split(text)
    except ValueError as exc:
        return f"not a command line: {exc}"
    return ""
