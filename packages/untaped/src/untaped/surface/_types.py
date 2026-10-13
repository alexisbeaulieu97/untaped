"""Coarse type tokens and default spellings for surface lines.

A type token is what a JSON reader sees, read from pydantic's JSON schema:
``string``, ``integer``, ``number``, ``boolean``, ``object``, ``array``, a
model's class name, ``T[]`` for an array of known items, ``?`` for nullable,
and a sorted ``a|b`` for any other union. Validation keywords follow in
parentheses (``string(minLength=1)``), since tightening one breaks a writer
and loosening one breaks a reader. A Literal's values are not in the token:
each becomes a ``choice=`` line, so adding one adds a line and dropping one
removes it. SDK parameters carry no type at all; only contract methods,
payload models, settings and implementer-shaped methods do.

Defaults are spelled as declared, the same on every machine: ``None`` (or
``unset`` in ``cli``/``cfg``), ``true``/``false``, numbers as is, strings bare
unless they need JSON quotes, collections as compact JSON, a path under
``HOME`` as ``~/…``, an enum member as ``E.MEMBER``, an exported object by
its exported name, and anything else as ``<computed>``.
"""

from __future__ import annotations

import collections.abc
import enum
import functools
import json
import operator
import re
import types
import typing
from collections.abc import Mapping
from pathlib import Path, PurePath
from typing import Any, Literal, get_args, get_origin

from pydantic import TypeAdapter
from pydantic.errors import PydanticSchemaGenerationError, PydanticUserError

COMPUTED = "<computed>"

#: JSON Schema keywords that change what a value may be (documentation keywords never do).
_CONSTRAINTS = frozenset(
    {
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }
)
_BARE = re.compile(r"^[^\s=\"]+$")
#: Factories whose value is the same on every call, so a default made by one is declared.
_PLAIN_FACTORIES: tuple[object, ...] = (list, dict, set, tuple, frozenset)


class TypeToken(typing.NamedTuple):
    """A coarse type and the Literal values it allows (``()`` when it allows any)."""

    token: str
    choices: tuple[object, ...]


def schema_of(annotation: object) -> dict[str, Any]:
    """The JSON schema of ``annotation``, models referred to by class name."""
    return TypeAdapter(annotation).json_schema(ref_template="{model}")


def type_token(annotation: object) -> TypeToken:
    """The coarse type of ``annotation`` and its Literal values.

    Raises pydantic's schema errors for a type with no JSON schema; a caller
    that can name such a type another way catches them (:func:`named_token`).
    """
    annotation = bounded(annotation)
    schema = schema_of(annotation)
    defs = _generic_titles(schema.get("$defs", {}), annotation)
    return TypeToken(_token(schema, defs, top=True), tuple(_choices(schema, defs)))


def _generic_titles(defs: Mapping[str, Any], annotation: object) -> Mapping[str, Any]:
    """``defs`` with each parametrized generic titled ``Name[Arg]`` from ``annotation``.

    pydantic names such a def after its own spelling (``PromptChoice_object_``),
    which a pydantic upgrade may respell; the annotation's spelling is ours.
    """
    spellings: dict[str, set[str]] = {}
    pending = [annotation]
    while pending:
        current = pending.pop()
        origin, args = get_origin(current), get_args(current)
        if isinstance(origin, type) and args and not _is_builtin(origin):
            spellings.setdefault(origin.__name__, set()).add(annotation_text(current))
        pending.extend(arg for arg in args if not isinstance(arg, list))
    found = dict(defs)
    for key, value in defs.items():
        for name, texts in spellings.items():
            if key.startswith(f"{name}_") and len(texts) == 1 and isinstance(value, Mapping):
                found[key] = {**value, "title": next(iter(texts))}
    return found


def _is_builtin(origin: type) -> bool:
    return origin.__module__ in ("builtins", "collections.abc", "typing")


def bounded(annotation: object) -> object:
    """``annotation`` with each type variable replaced by its bound, or ``object``.

    A type variable promises only its bound, so ``PromptChoice[T]`` reads as
    ``PromptChoice[object]`` whatever the variable is named.
    """
    if isinstance(annotation, typing.TypeVar):
        bound = annotation.__bound__
        return object if bound is None else bounded(bound)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is None or not args or origin is Literal or origin is collections.abc.Callable:
        return annotation
    if origin is typing.Annotated:
        return typing.Annotated[bounded(args[0]), *annotation.__metadata__]  # type: ignore[attr-defined]
    new = tuple(bounded(arg) for arg in args)
    if new == args:
        return annotation
    if origin in (types.UnionType, typing.Union):
        return functools.reduce(operator.or_, new)
    return origin[new[0] if len(new) == 1 else new]


def node_token(schema: Mapping[str, Any], defs: Mapping[str, Any]) -> TypeToken:
    """The coarse type and Literal values of one node of a JSON schema (a model's property).

    ``defs`` are the schema's ``$defs``: a reference to a model reads as the
    model's class name, one to anything else (a named alias) as what it names.
    """
    return TypeToken(_token(schema, defs), tuple(_choices(schema, defs)))


def named_token(annotation: object) -> TypeToken:
    """:func:`type_token`, or the annotation's own spelling when it has no JSON schema."""
    try:
        return type_token(annotation)
    except PydanticSchemaGenerationError, PydanticUserError:
        return TypeToken(annotation_text(annotation), ())


def annotation_text(annotation: object) -> str:
    """A stable spelling of a Python annotation: ``Screen``, ``Sequence[str]``, ``A | None``."""
    if annotation is None or annotation is type(None):
        return "None"
    if isinstance(annotation, typing.TypeVar):
        bound = annotation.__bound__
        return "object" if bound is None else annotation_text(bound)
    if isinstance(annotation, typing.ParamSpec):
        return annotation.__name__
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (types.UnionType, typing.Union):
        return " | ".join(annotation_text(arg) for arg in args)
    if origin is not None:
        name = getattr(origin, "__name__", None) or str(origin).removeprefix("typing.")
        inner = ", ".join(
            "[" + ", ".join(annotation_text(a) for a in arg) + "]"
            if isinstance(arg, list)
            else "..."
            if arg is Ellipsis
            else annotation_text(arg)
            for arg in args
        )
        return f"{name}[{inner}]"
    if isinstance(annotation, type):
        return annotation.__name__
    if isinstance(annotation, typing.ForwardRef):
        return annotation.__forward_arg__
    return str(annotation).removeprefix("typing.")


def _target(schema: Mapping[str, Any], defs: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """What a ``$ref`` to a named alias stands for (``None``: not a ref, or a model's)."""
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return None
    target = defs.get(ref)
    if not isinstance(target, Mapping) or "properties" in target or target.get("type") == "object":
        return None
    return target


def _token(schema: Mapping[str, Any], defs: Mapping[str, Any], *, top: bool = False) -> str:
    target = _target(schema, defs)
    if target is not None:
        return _token(target, defs)
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return _ref_name(ref, defs)
    if top and schema.get("type") == "object" and "properties" in schema and "title" in schema:
        return str(schema["title"])
    for key in ("anyOf", "oneOf"):
        members = schema.get(key)
        if isinstance(members, list):
            return _union(members, defs)
    kind = schema.get("type")
    if isinstance(kind, list):
        return _union([{"type": each} for each in kind], defs)
    if "enum" in schema or "const" in schema:
        values = schema.get("enum", [schema.get("const")])
        kind = kind or _json_type(values)
    if kind is None:
        return "any"
    if kind == "array":
        items = schema.get("items")
        if isinstance(items, Mapping) and items:
            return f"{_token(items, defs)}[]{_constraints(schema)}"
    return f"{kind}{_constraints(schema)}"


def _ref_name(ref: str, defs: Mapping[str, Any]) -> str:
    """A model's name: its class name, ``M[int]`` for a generic one (not ``M_int_``)."""
    title = defs.get(ref, {}).get("title")
    if isinstance(title, str) and title != ref and ref.startswith(title.partition("[")[0] + "_"):
        return title
    return ref


def _union(members: list[Mapping[str, Any]], defs: Mapping[str, Any]) -> str:
    nullable = any(member.get("type") == "null" for member in members)
    tokens = sorted({_token(member, defs) for member in members if member.get("type") != "null"})
    joined = "|".join(tokens) or "null"
    return f"{joined}?" if nullable and tokens else joined


def _json_type(values: list[object]) -> str:
    kinds = sorted({_python_json_type(value) for value in values})
    return "|".join(kinds)


def _python_json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _constraints(schema: Mapping[str, Any]) -> str:
    found = {key: schema[key] for key in schema if key in _CONSTRAINTS}
    if not found:
        return ""
    return "(" + ",".join(f"{key}={_scalar(found[key])}" for key in sorted(found)) + ")"


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value if _BARE.match(value) else json.dumps(value)
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _choices(schema: Mapping[str, Any], defs: Mapping[str, Any]) -> list[object]:
    target = _target(schema, defs)
    if target is not None:
        return _choices(target, defs)
    if "enum" in schema:
        return list(schema["enum"])
    if "const" in schema:
        return [schema["const"]]
    found: list[object] = []
    for key in ("anyOf", "oneOf"):
        for member in schema.get(key, ()):
            found.extend(value for value in _choices(member, defs) if value not in found)
    return found


def literal_values(annotation: object) -> tuple[object, ...] | None:
    """A ``Literal``'s values, looked for through ``X | None`` and ``Annotated``; else None."""
    origin = get_origin(annotation)
    if origin is Literal:
        return get_args(annotation)
    if origin is typing.Annotated:
        return literal_values(get_args(annotation)[0])
    if origin in (types.UnionType, typing.Union):
        values = [literal_values(arg) for arg in get_args(annotation) if arg is not type(None)]
        if len(values) == 1:
            return values[0]
    return None


def default_text(
    value: object, *, none: str = "None", names: Mapping[int, str] | None = None
) -> str:
    """How a declared default is written in a line (``none`` is how ``None`` reads)."""
    if value is None:
        return none
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, str | PurePath):
        return _string(home_relative(value))
    if names is not None and id(value) in names:
        return names[id(value)]
    if isinstance(value, list | tuple | dict | set | frozenset):
        try:
            return json.dumps(_plain(value), separators=(",", ":"), sort_keys=True)
        except TypeError:
            return COMPUTED
    return COMPUTED


def factory_default(factory: object) -> object:
    """The value a plain container factory makes, or :data:`COMPUTED` for any other factory."""
    if factory in _PLAIN_FACTORIES:
        return typing.cast("type", factory)()
    return _Computed()


class _Computed:
    """Stands for a default made by a factory: :func:`default_text` spells it ``<computed>``."""


def home_relative(value: str | PurePath) -> str:
    """``value`` as text, a path under ``HOME`` written ``~/…``."""
    text = str(value)
    home = str(Path.home())
    if home not in ("", "/") and (text == home or text.startswith(home + "/")):
        return "~" + text[len(home) :]
    return text


def _string(text: str) -> str:
    return text if _BARE.match(text) else json.dumps(text)


def _plain(value: object) -> object:
    if isinstance(value, set | frozenset):
        return sorted(_plain(item) for item in value)  # type: ignore[type-var]
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, PurePath):
        return home_relative(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"{value!r} is not plain data")


def choice_text(value: object) -> str:
    """A Literal value as a ``choice=`` line writes it."""
    if isinstance(value, str):
        return _string(value)
    return default_text(value)
