"""Walk a Pydantic ``Settings`` model into a flat list of leaf descriptors.

Used by the root ``untaped config list/set/unset`` commands to enumerate
what's configurable without hard-coding the schema. Lists, dicts, and other
collection types are skipped unless ``include_collections`` asks for them as
whole-value leaves (the ``config`` commands do, e.g. ``untaped.ui.symbols``).
"""

from __future__ import annotations

import copy
import types
import typing
import weakref
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Annotated, Any, get_args, get_origin

import annotated_types
from pydantic import BaseModel, SecretStr
from pydantic_core import PydanticUndefined

from untaped.runtime.redaction import redact_url_password
from untaped.sdk._keys import ENTRY_NAME, ENTRY_NAME_RULE, map_value_type, nested_model

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo


@dataclass(frozen=True)
class Choices:
    """``Annotated`` marker: the names a setting accepts, for a set its type can't carry.

    On a ``str`` field the value must be one of them (``untaped.ui.theme``); on a
    mapping, its keys (``untaped.ui.symbols``). A ``Literal`` needs no marker. The
    field's write-time validator is the only reader that enforces it; doctor,
    the config reference and ``config set --prompt`` read the same names, so
    none of them keeps its own list.
    """

    source: Callable[[], Iterable[str]]
    """Returns the names; called on each read, so it may name things defined later."""

    label: str
    """Plural noun for messages and docs (``themes``, ``color roles``)."""

    noun: str = "name"
    """Singular noun for what is unknown (``UI theme``)."""

    defaults: Callable[[Any], Mapping[str, str]] | None = None
    """For a mapping: each name's value while the mapping doesn't set it, given the validated
    model that holds the field (``untaped.ui.symbols`` reads the chosen theme's symbols)."""

    defaults_label: str = "default"
    """Where :attr:`defaults` come from, as a source is shown (``theme``)."""

    def names(self) -> tuple[str, ...]:
        """The accepted names, in declared order."""
        return tuple(self.source())

    def refusal(self, value: object, *, prefix: str | None = None) -> str | None:
        """Why ``value`` is refused, listing the accepted names; ``None`` when accepted.

        ``value`` is a string, or a mapping whose keys are checked; ``prefix``
        (``untaped.ui.symbols``) qualifies each stray key.
        """
        names = self.names()
        valid = ", ".join(sorted(names))
        if not isinstance(value, Mapping):
            return (
                None
                if value in names
                else f"unknown {self.noun} {value!r}; valid {self.label}: {valid}"
            )
        stray = sorted(str(key) for key in value if key not in names)
        if not stray:
            return None
        shown = ", ".join(f"{prefix}.{name}" if prefix else name for name in stray)
        return f"unknown {self.noun} {shown}. Valid {self.label}: {valid}"


@dataclass(frozen=True)
class Argv:
    """``Annotated`` marker: a ``list[str]`` that is a command's argv, run without a shell.

    An editor shows it as one shell-quoted line (``config set`` still takes
    JSON); checking the argv stays the field's own validator.
    """


@dataclass(frozen=True)
class HomeOnly:
    """``Annotated`` marker: a setting only the home's base sets: no project file, no profile.

    Mark a setting that locates or describes what a whole home shares (a
    store's folder, an index, this machine's choices): a project or a profile
    that set its own would split it, or make it depend on the folder a command
    runs in. A project's ``.untaped/config.yml`` or a picked profile setting
    one is refused (exit 4); ``UNTAPED_*`` variables still apply. A setting a
    ``Dir(at=...)`` places is home-only without the mark. Annotate the field
    itself: ``Annotated[Path | None, HomeOnly()]``.

    First consumer: dotfiles' machine choices (``tags``, ``os``) in its redesign.
    """


#: Each settings model's settings a ``Dir(at=...)`` places, by path inside the model.
#: Composition fills it from the plugin's declaration; those settings are home-only.
_PLACED: weakref.WeakKeyDictionary[type[BaseModel], frozenset[tuple[str, ...]]] = (
    weakref.WeakKeyDictionary()
)


def place_home_only(model: type[BaseModel], paths: Iterable[tuple[str, ...]]) -> None:
    """Record that a ``Dir`` places each of ``paths`` of ``model``, which makes them home-only.

    :func:`walk_settings` marks them as :class:`HomeOnly` would, in
    ``model`` and in any model derived from it (the composed section model);
    a call replaces what ``model`` had.
    """
    _PLACED[model] = frozenset(paths)


def _placed(model_cls: type[BaseModel]) -> frozenset[tuple[str, ...]]:
    """The paths a ``Dir`` places in ``model_cls`` or a model it derives from."""
    return frozenset().union(
        *(_PLACED.get(cls, frozenset()) for cls in model_cls.__mro__ if isinstance(cls, type))
    )


def field_choices(field: FieldInfo) -> Choices | None:
    """The field's :class:`Choices` marker, if it has one."""
    return next((item for item in field.metadata if isinstance(item, Choices)), None)


@dataclass(frozen=True)
class FieldDescriptor:
    """One configurable leaf in the settings model."""

    path: tuple[str, ...]
    """Dotted path components, e.g. ``("awx", "token")``."""

    annotation: type[Any]
    """Resolved Python type at the leaf (``Optional[X]`` is unwrapped to ``X``)."""

    default: Any
    """Default value if ``has_default`` is ``True``, else ``None``."""

    has_default: bool
    """Whether the model declares a default value."""

    is_secret: bool
    """``True`` for ``SecretStr`` fields — render as ``***`` unless explicitly revealed."""

    optional: bool = False
    """``True`` when the field's annotation is ``X | None`` (``annotation`` is then ``X``)."""

    description: str | None = None
    """The field's ``description`` (``FieldInfo.description``), ``None`` when it has none."""

    choices: Choices | None = None
    """The field's :class:`Choices` marker, when it accepts a declared set of names."""

    bounds: tuple[annotated_types.BaseMetadata, ...] = ()
    """The field's numeric bounds (``Gt``, ``Ge``, ``Lt``, ``Le``), read for help text only."""

    argv: bool = False
    """Whether the field carries the :class:`Argv` marker: a command's argv."""

    home_only: bool = False
    """Whether only the home's base sets it: a :class:`HomeOnly` mark, or a ``Dir``'s place."""

    entry_depth: int = 0
    """The path length of the map entry that holds it, ``0`` when no entry does.

    A key steps into a map setting by entry name (:func:`find_descriptor`):
    a write never removes that entry, and the setting has a value only while
    the entry exists. An entry's own descriptor counts only an entry above it.
    """

    @property
    def key(self) -> str:
        """Dotted key, e.g. ``"untaped.http.proxy"``."""
        return ".".join(self.path)

    @property
    def is_collection(self) -> bool:
        """Whether the leaf holds a whole mapping or list (e.g. ``untaped.ui.symbols``).

        A map entry whose value is a model holds a mapping too: it is set as
        JSON or YAML and shown as one.
        """
        return _is_collection(self.annotation) or _is_model(self.annotation)

    @property
    def holds_secret(self) -> bool:
        """Whether the value is a secret or holds one (a map of secrets, a model with one)."""
        return self.is_secret or _holds_secret(self.annotation, frozenset())


def walk_settings(
    model_cls: type[BaseModel],
    _prefix: tuple[str, ...] = (),
    *,
    include_collections: bool = False,
    _home: frozenset[tuple[str, ...]] = frozenset(),
) -> list[FieldDescriptor]:
    """Return every leaf scalar field of ``model_cls``, recursing into nested models.

    ``include_collections`` also returns list/dict fields as single leaves.
    """
    entries: list[FieldDescriptor] = []
    placed = _home | _placed(model_cls)
    for name, field in model_cls.model_fields.items():
        annotation = unwrap_optional(field.annotation)
        path = (*_prefix, name)

        nested = nested_model(field.annotation)
        if nested is not None:
            inner = frozenset(each[1:] for each in placed if len(each) > 1 and each[0] == name)
            entries.extend(
                walk_settings(nested, path, include_collections=include_collections, _home=inner)
            )
            continue

        if _is_collection(annotation) and not include_collections:
            continue

        if field.default is not PydanticUndefined:
            default: Any = field.default
            has_default = True
        elif field.default_factory is not None:
            # Pydantic accepts both zero-arg and one-arg (init values) factories;
            # for our settings schema only zero-arg factories are used.
            default = field.default_factory()  # type: ignore[call-arg]
            has_default = True
        else:
            default = None
            has_default = False

        entries.append(
            FieldDescriptor(
                path=path,
                annotation=annotation,
                default=default,
                has_default=has_default,
                is_secret=annotation is SecretStr,
                optional=_is_optional(field.annotation),
                description=field.description,
                choices=field_choices(field),
                bounds=tuple(item for item in field.metadata if isinstance(item, _BOUNDS)),
                argv=any(isinstance(item, Argv) for item in field.metadata),
                home_only=(name,) in placed
                or any(isinstance(item, HomeOnly) for item in field.metadata),
            )
        )
    return entries


_BOUNDS = (annotated_types.Gt, annotated_types.Ge, annotated_types.Lt, annotated_types.Le)


def bounds_text(descriptor: FieldDescriptor) -> str | None:
    """The field's bounds as a sentence (``Above 0.``, ``1 to 32.``); ``None`` when it has none.

    For help text (the setup screen, the config reference): the model's
    validation is what enforces them.
    """
    gt = ge = lt = le = None
    for item in descriptor.bounds:
        match item:
            case annotated_types.Gt(gt=value):
                gt = value
            case annotated_types.Ge(ge=value):
                ge = value
            case annotated_types.Lt(lt=value):
                lt = value
            case annotated_types.Le(le=value):
                le = value
    if ge is not None and le is not None:
        return f"{ge} to {le}."
    low = f"above {gt}" if gt is not None else None if ge is None else f"{ge} or more"
    high = f"below {lt}" if lt is not None else None if le is None else f"at most {le}"
    text = " and ".join(part for part in (low, high) if part)
    return f"{text[0].upper()}{text[1:]}." if text else None


class EntryNameError(ValueError):
    """A key names a map entry no key can address; the message says to set the whole map."""


def find_descriptor(descriptors: list[FieldDescriptor], key: str) -> FieldDescriptor | None:
    """Return the descriptor matching the dotted ``key``, or ``None``.

    ``descriptors`` are :func:`walk_settings`' (with ``include_collections``).
    A key that goes past a ``dict[str, X]`` setting takes the next segment as
    an entry name and continues into ``X``: a model's fields, a map's
    entries, or ``X`` itself. Such a descriptor carries the full path and
    ``X``'s type, and is home-only when the map is. An entry name that
    :data:`ENTRY_NAME` doesn't match raises :class:`EntryNameError`.
    """
    return _find(descriptors, tuple(key.split(".")))


def _find(descriptors: list[FieldDescriptor], parts: tuple[str, ...]) -> FieldDescriptor | None:
    for descriptor in descriptors:
        if descriptor.path == parts:
            return descriptor
        depth = len(descriptor.path)
        if parts[:depth] == descriptor.path and map_value_type(descriptor.annotation) is not None:
            return _within(descriptor, parts[depth:])
    return None


def _within(
    holder: FieldDescriptor, rest: tuple[str, ...], *, entry: bool = False
) -> FieldDescriptor | None:
    """The descriptor ``rest`` reaches from ``holder``, a map or (``entry``) a map's entry."""
    if not rest:
        return holder
    if map_value_type(holder.annotation) is not None:
        above = len(holder.path) if entry else holder.entry_depth
        return _within(_entry(holder, rest[0], above), rest[1:], entry=True)
    if not _is_model(holder.annotation):
        return None
    inner = walk_settings(holder.annotation, holder.path, include_collections=True)
    return _find(
        [
            replace(
                each,
                home_only=each.home_only or holder.home_only,
                entry_depth=len(holder.path),
            )
            for each in inner
        ],
        (*holder.path, *rest),
    )


def _entry(holder: FieldDescriptor, name: str, above: int) -> FieldDescriptor:
    """The descriptor of entry ``name`` of map ``holder``; ``above`` is the entry holding it."""
    if ENTRY_NAME.fullmatch(name) is None:
        raise EntryNameError(
            f"{holder.key} entry {name!r} can't be named in a key: {ENTRY_NAME_RULE}; "
            f"set the whole map instead (config set {holder.key} VALUE)"
        )
    value = get_args(holder.annotation)[1]
    metadata: tuple[Any, ...] = ()
    if get_origin(value) is Annotated:
        value, *rest = get_args(value)
        metadata = tuple(rest)
    annotation = unwrap_optional(value)
    return FieldDescriptor(
        path=(*holder.path, name),
        annotation=annotation,
        default=None,
        has_default=False,
        is_secret=annotation is SecretStr,
        optional=_is_optional(value),
        bounds=tuple(item for item in metadata if isinstance(item, _BOUNDS)),
        argv=any(isinstance(item, Argv) for item in metadata),
        home_only=holder.home_only,
        entry_depth=above,
    )


def secret_field_paths(model_cls: type[BaseModel]) -> list[tuple[str, ...]]:
    """Return the dotted paths of every ``SecretStr``-typed leaf in ``model_cls``."""
    return [d.path for d in walk_settings(model_cls) if d.is_secret]


def redact_nested_url_passwords(value: Any, *, placeholder: str = "***") -> Any:
    """Copy of ``value`` with :func:`redact_url_password` applied to every string."""
    return _redact_url_passwords(copy.deepcopy(value), placeholder)


def redact_secrets(
    data: Mapping[str, Any],
    paths: Iterable[tuple[str, ...]],
    *,
    placeholder: str = "***",
) -> dict[str, Any]:
    """Deep-copy ``data`` and replace each leaf at ``paths`` with ``placeholder``.

    Paths that aren't present in ``data`` are silently skipped — profiles can
    omit any subset of the schema. ``None`` leaves are also left alone, so a
    user who has not set a secret still sees ``None`` rather than ``***``.
    Passwords embedded in URL strings anywhere in ``data`` are masked too
    (see :func:`redact_url_password`).
    """
    out: dict[str, Any] = _redact_url_passwords(copy.deepcopy(dict(data)), placeholder)
    for path in paths:
        _redact_path(out, path, placeholder)
    return out


def _redact_url_passwords(value: Any, placeholder: str) -> Any:
    if isinstance(value, str):
        return redact_url_password(value, placeholder=placeholder)
    if isinstance(value, dict):
        return {key: _redact_url_passwords(item, placeholder) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_url_passwords(item, placeholder) for item in value]
    return value


def _redact_path(data: dict[str, Any], path: tuple[str, ...], placeholder: str) -> None:
    if not path:
        return
    cursor: Any = data
    for part in path[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            return
        cursor = cursor[part]
    leaf = path[-1]
    if isinstance(cursor, dict) and leaf in cursor and cursor[leaf] is not None:
        cursor[leaf] = placeholder


def unwrap_optional(annotation: Any) -> Any:
    """``X`` for ``X | None`` (or ``Optional[X]``); any other annotation unchanged."""
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _is_optional(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return (origin is types.UnionType or origin is typing.Union) and type(None) in get_args(
        annotation
    )


def _is_collection(annotation: Any) -> bool:
    return get_origin(annotation) in (list, dict, set, tuple, frozenset)


def _holds_secret(annotation: Any, seen: frozenset[type]) -> bool:
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if annotation is SecretStr:
        return True
    if _is_model(annotation):
        if annotation in seen:
            return False
        inner = seen | {annotation}
        return any(_holds_secret(f.annotation, inner) for f in annotation.model_fields.values())
    return any(_holds_secret(arg, seen) for arg in get_args(annotation))


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)
