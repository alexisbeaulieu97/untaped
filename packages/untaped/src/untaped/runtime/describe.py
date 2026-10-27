"""What a page says of a model, a contract or an option type, in a reader's words.

Composition reads each kind's model, each guide's class and each offered
contract through these functions once and keeps what they say in the
declaration, so ``untaped docs`` renders its pages without importing a
plugin module. The settings page spells a setting's type with
:func:`type_text` too, so a type reads one way on every page: ``text``,
``list of case``, ``map of name to case``, ```pass`, `fail`, or null``.
"""

from __future__ import annotations

import collections.abc
import datetime
import enum
import inspect
import re
import types
import typing
from pathlib import Path, PurePath
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, SecretStr
from pydantic_core import PydanticUndefined, to_jsonable_python

from untaped.runtime.declaration import FieldDecl, MethodDecl

_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")
_LISTS = frozenset({list, tuple, set, frozenset, collections.abc.Sequence, collections.abc.Set})
_MAPS = frozenset({dict, collections.abc.Mapping})
_NAMED: dict[object, str] = {
    str: "text",
    int: "integer",
    float: "number",
    bool: "true/false",
    Path: "path",
    PurePath: "path",
    SecretStr: "secret",
    datetime.datetime: "date and time",
    datetime.date: "date",
    datetime.timedelta: "duration",
    Any: "any value",
    object: "any value",
}
#: The ``holds`` of a field holding models, by the shape holding them.
type Holds = Literal["", "[]", ".NAME"]


def prose(cls: type) -> tuple[str, str]:
    """The summary (first line) and the whole docstring of ``cls`` (its own, never inherited)."""
    doc = inspect.cleandoc(cls.__dict__.get("__doc__") or "")
    return doc.partition("\n")[0].strip(), doc


def first_paragraph(doc: str) -> str:
    """The first paragraph of ``doc``, its lines joined: what a list shows of a model or guide."""
    paragraph = inspect.cleandoc(doc).partition("\n\n")[0]
    return " ".join(paragraph.split())


def noun(cls: type) -> str:
    """A class name as a reader says it: ``TestResult`` is ``test result``."""
    return " ".join(word.lower() for word in _WORDS.findall(cls.__name__))


def type_text(annotation: object) -> str:
    """``annotation`` in a reader's words; ``None`` in a union reads ``or null``."""
    annotation = _peel(annotation)
    if _is_union(annotation):
        parts = [arg for arg in get_args(annotation) if arg is not type(None)]
        nullable = len(parts) < len(get_args(annotation))
        if all(_choices(part) for part in parts):
            text = ", ".join(f"`{choice}`" for part in parts for choice in _choices(part))
            return f"{text}, or null" if nullable else text
        text = " or ".join(type_text(part) for part in parts)
        return f"{text} or null" if nullable else text
    choices = _choices(annotation)
    if choices:
        return ", ".join(f"`{choice}`" for choice in choices)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in _LISTS:
        return f"list of {type_text(args[0])}" if args else "list"
    if origin in _MAPS:
        return f"map of name to {type_text(args[1])}" if len(args) == 2 else "map"
    if annotation in _NAMED:
        return _NAMED[annotation]
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return noun(annotation)
        for base, name in _NAMED.items():
            if isinstance(base, type) and base is not object and issubclass(annotation, base):
                return name
        return noun(annotation)
    return str(annotation)


def fields_of(model: type[BaseModel], seen: frozenset[type] = frozenset()) -> tuple[FieldDecl, ...]:
    """Every field of ``model`` with the fields of the models it holds, a level each.

    A field's name is the key a file or a row spells (its alias when it has
    one). A model met again inside itself holds no fields, so the levels end.
    """
    seen = seen | {model}
    found: list[FieldDecl] = []
    for name, info in model.model_fields.items():
        held, holds = _held(info.annotation)
        default = None
        if not info.is_required() and info.default is not PydanticUndefined:
            default = jsonable(info.default)
        found.append(
            FieldDecl(
                name=info.serialization_alias or info.alias or name,
                type=type_text(info.annotation),
                required=info.is_required(),
                default=default,
                description=info.description or "",
                holds=holds if held is not None and held not in seen else "",
                fields=() if held is None or held in seen else fields_of(held, seen),
            )
        )
    return tuple(found)


def method_decl(name: str, function: object, hints: typing.Mapping[str, Any]) -> MethodDecl:
    """A contract method as its point's page lists it: signature, first line, required."""
    parameters = [
        f"{parameter}: {_hint(hints[parameter])}" if parameter in hints else parameter
        for parameter in list(inspect.signature(typing.cast("Any", function)).parameters)[1:]
    ]
    returned = f" -> {_hint(hints['return'])}" if "return" in hints else ""
    doc = inspect.cleandoc(getattr(function, "__doc__", None) or "")
    return MethodDecl(
        name=name,
        signature=f"{name}({', '.join(parameters)}){returned}",
        summary=doc.partition("\n")[0].strip(),
        required=bool(getattr(function, "__isabstractmethod__", False)),
    )


def _hint(hint: object) -> str:
    """A type hint as code spells it, without module paths: ``list[Book]``."""
    if isinstance(hint, type) and not get_args(hint):
        return hint.__name__
    if isinstance(hint, typing.TypeVar):
        return hint.__name__
    return re.sub(r"\b(?:[A-Za-z_][\w]*\.)+([A-Za-z_]\w*)", r"\1", repr(hint)).replace(
        "NoneType", "None"
    )


def _held(annotation: object) -> tuple[type[BaseModel] | None, Holds]:
    """The model a field holds, and how: itself, a list of them, or a map of name to them."""
    annotation = _peel(annotation)
    if _is_union(annotation):
        parts = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _held(parts[0]) if len(parts) == 1 else (None, "")
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation, ""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in _LISTS and args:
        inner = _peel(args[0])
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return inner, "[]"
    if origin in _MAPS and len(args) == 2:
        inner = _peel(args[1])
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return inner, ".NAME"
    return None, ""


def _peel(annotation: object) -> object:
    """``annotation`` without ``Annotated``'s marks or a ``type`` alias's name."""
    while True:
        if isinstance(annotation, typing.TypeAliasType):
            annotation = annotation.__value__
        elif get_origin(annotation) is typing.Annotated:
            annotation = get_args(annotation)[0]
        else:
            return annotation


def _is_union(annotation: object) -> bool:
    return get_origin(annotation) in (typing.Union, types.UnionType)


def _choices(annotation: object) -> tuple[str, ...]:
    """The values a ``Literal`` or an enum allows, as a file spells them; ``()`` otherwise."""
    annotation = _peel(annotation)
    if get_origin(annotation) is Literal:
        return tuple(str(jsonable(value)) for value in get_args(annotation))
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return tuple(str(member.value) for member in annotation)
    return ()


def jsonable(value: object) -> Any:
    """``value`` as JSON (an enum by its value); ``None`` when it has no JSON form."""
    if isinstance(value, enum.Enum):
        return value.value
    try:
        return to_jsonable_python(value)
    except Exception:
        return None
