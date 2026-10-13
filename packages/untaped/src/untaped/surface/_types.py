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
from contextvars import ContextVar
from pathlib import Path, PurePath
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, TypeAdapter
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
    defs = generic_names(schema.get("$defs", {}), annotation)
    name = generic_name(annotation)
    if name is not None and "$ref" not in schema:
        schema = {**schema, _NAME: name}
    return TypeToken(_token(schema, defs, top=True), tuple(_choices(schema, defs)))


#: The key under which a def carries our spelling of its parametrized generic.
_NAME = "x-untaped-name"
#: Set while spelling a generic's argument: the names then follow type arguments only,
#: never a model's fields, so a model that refers to itself (``kids: list[Page[Node]]``)
#: reads by its name instead of recursing.
_ARGUMENT: ContextVar[bool] = ContextVar("_ARGUMENT", default=False)


def generic_parts(annotation: object) -> tuple[type, tuple[object, ...]] | None:
    """A parametrized generic class and its arguments (pydantic models included).

    An unparametrized generic model (``Page``, which pydantic also returns for
    ``Page[T]`` with its own parameter) has its type variables as arguments.
    """
    metadata = getattr(annotation, "__pydantic_generic_metadata__", None)
    if isinstance(annotation, type) and metadata:
        if metadata.get("origin") is not None:
            return metadata["origin"], tuple(metadata["args"])
        if metadata.get("parameters"):
            return annotation, tuple(metadata["parameters"])
    origin, args = get_origin(annotation), get_args(annotation)
    if isinstance(origin, type) and args and not _is_builtin(origin):
        return origin, args
    return None


def generic_name(annotation: object) -> str | None:
    """``Name[arg,…]`` for a parametrized generic, each argument as its type token.

    Our spelling, never pydantic's def name (``PromptChoice_object_``), which a
    pydantic upgrade may respell. A type variable's argument is its bound, else
    ``object``; a Literal's values follow its token (``string(choice=a|b)``),
    since they have no ``choice=`` line of their own there.
    """
    parts = generic_parts(annotation)
    if parts is None:
        return None
    origin, args = parts
    return f"{origin.__name__}[{','.join(_argument(arg) for arg in args)}]"


def _argument(arg: object) -> str:
    arg = bounded(arg)
    if arg is object:
        return "object"
    reset = _ARGUMENT.set(True)
    try:
        token = type_token(arg)
    finally:
        _ARGUMENT.reset(reset)
    if not token.choices:
        return token.token
    return f"{token.token}(choice={'|'.join(sorted(_choice(v) for v in token.choices))})"


def _choice(value: object) -> str:
    """A Literal value inside an argument, quoted when it holds the spelling's own marks."""
    text = choice_text(value)
    return json.dumps(text) if _ARGUMENT_MARKS.search(text) and text[:1] != '"' else text


_ARGUMENT_MARKS = re.compile(r"[|()\[\],]")


def generic_names(defs: Mapping[str, Any], annotation: object) -> Mapping[str, Any]:
    """``defs`` with each parametrized generic reached from ``annotation`` named by
    :func:`generic_name`, found by the def pydantic gives it (never by its name).

    The walk goes through type arguments and model fields; while an argument is
    being spelled, through type arguments only.
    """
    found = dict(defs)
    seen: set[int] = set()
    pending = [annotation]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        parts = generic_parts(current)
        if parts is not None:
            ref = schema_of(list[current])["items"].get("$ref")  # type: ignore[valid-type]
            if isinstance(ref, str) and isinstance(found.get(ref), Mapping):
                found[ref] = {**found[ref], _NAME: generic_name(current)}
            pending.extend(parts[1])
        else:
            pending.extend(arg for arg in get_args(current) if not isinstance(arg, list))
            value = _alias_value(current)
            if value is not None:
                pending.append(value)
        if not _ARGUMENT.get() and isinstance(current, type) and issubclass(current, BaseModel):
            pending.extend(info.annotation for info in current.model_fields.values())
    return found


def _alias_value(annotation: object) -> object | None:
    """What a ``type`` alias stands for, its arguments filled in (``Pair[int]``); else None."""
    if isinstance(annotation, typing.TypeAliasType):
        return typing.cast("object", annotation.__value__)
    origin = get_origin(annotation)
    if not isinstance(origin, typing.TypeAliasType):
        return None
    value: object = origin.__value__
    args = get_args(annotation)
    try:
        return typing.cast("object", value[args[0] if len(args) == 1 else args])  # type: ignore[index]
    except TypeError:
        return value


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
    parts = generic_parts(annotation)
    if parts is not None and get_origin(annotation) is None:  # a pydantic generic model
        model, model_args = parts
        bound_args = tuple(bounded(arg) for arg in model_args)
        if bound_args == model_args:
            return annotation
        return model[bound_args[0] if len(bound_args) == 1 else bound_args]  # type: ignore[index]
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
    if top and isinstance(schema.get(_NAME), str):
        return str(schema[_NAME])
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
    """A model's name: its class name, or ``Name[arg]`` for a parametrized generic."""
    name = defs.get(ref, {}).get(_NAME)
    return name if isinstance(name, str) else ref


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
