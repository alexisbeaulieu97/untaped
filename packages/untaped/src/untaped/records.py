"""Base models for the records commands emit, plus the timestamp type.

Plugin output models subclass these bases so every kind shares one shape
for the fields the pipe contract fixes (``docs/reference/conventions.md#output-records``):

- :class:`OutcomeRecord` — a mutation result with an ``action`` from the
  outcome vocabulary (``created``, ``updated``, ``failed``, ...);
- :class:`TargetRecord` — a record about a filesystem target, carrying an
  absolute ``target_path``;
- :class:`CheckRecord` — a check result with a ``status`` from the check
  vocabulary (``pass``/``warn``/``fail``/``error``);
- ``error`` — the optional :class:`~untaped.diagnostics.ErrorInfo` of a failed
  outcome or target row (``category``, ``system``, ``retryable``, ``message``,
  ``hint``);
- :data:`UtcTimestamp` — a ``datetime`` normalized to UTC that serializes as
  RFC 3339 with a ``Z`` suffix (``2026-01-02T03:04:05Z``), to the second in
  tables and in full everywhere else;
- :class:`TableGlyph` — a field annotation that shows a value as a glyph in
  tables only.

A record type picks the columns a table of it shows by default with a
``table_columns`` class variable (:func:`table_columns_of`).

Records are frozen pydantic models; subclasses add their own fields, which
serialize before the base fields they inherit (the identifying field stays
first for ``--format raw`` and the first table column), except that an
inherited ``action`` follows the identifying field.

A record type declares its kind as a class keyword,
``class GitlabProject(Record, kind="gitlab.project")``; :func:`emit
<untaped.cli.emit>` reads it from the rows, and :func:`record_model` maps a
kind back to its model. Every record's fields must survive a JSON round trip,
which the class definition checks (:class:`Record`).
"""

from __future__ import annotations

import annotationlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import (
    Annotated,
    Any,
    ClassVar,
    Final,
    Literal,
    TypeAliasType,
    get_args,
    get_origin,
)

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    SecretBytes,
    SecretStr,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    WrapSerializer,
    model_serializer,
)
from pydantic.fields import FieldInfo

from untaped.diagnostics import ErrorInfo


def _to_utc(value: datetime) -> datetime:
    """Normalize to an aware UTC datetime (naive values are taken as UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Render ``value`` as RFC 3339 UTC with a ``Z`` suffix, to the second."""
    return _to_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


#: The serialization context of a dump for a ``table``, which shows
#: timestamps to the second (:data:`UtcTimestamp`).
TABLE_CONTEXT: Final = MappingProxyType({"untaped.table": True})


def _utc_json(value: datetime, info: SerializationInfo) -> str:
    """RFC 3339 UTC with a ``Z``: to the second in a table, else in full (lossless)."""
    context = info.context
    if isinstance(context, Mapping) and context.get("untaped.table"):
        return format_utc(value)
    return _to_utc(value).isoformat().replace("+00:00", "Z")


UtcTimestamp = Annotated[
    datetime,
    AfterValidator(_to_utc),
    PlainSerializer(_utc_json, return_type=str, when_used="json"),
]
"""A UTC ``datetime`` field that renders as ``2026-01-02T03:04:05Z`` in output.

Tables show it to the second; every other format keeps the microseconds
(``2026-01-02T03:04:05.000678Z``), so it reads back unchanged. Name such
fields ``<event>_at`` (``created_at``, ``scanned_at``)."""

#: Serializers known to round-trip, allowed on :class:`Record` fields.
_ROUND_TRIP_SERIALIZERS: Final = frozenset({_utc_json})


#: The kind grammar: two or three lowercase dot-separated segments. The first
#: is the declaring plugin's name verbatim, hyphens included (``untaped`` for
#: core); the others are snake_case.
KIND_PATTERN: Final = re.compile(
    r"^[a-z][a-z0-9]*(-[a-z0-9]+)*(\.[a-z][a-z0-9]*(_[a-z0-9]+)*){1,2}$"
)


def check_data_kind(kind: str) -> None:
    """Reject a record kind that is not ``<plugin>.<noun>`` or ``<plugin>.<noun>.summary``.

    ``.summary`` is the only third segment a data kind may have: pipe
    consumers that skip informational records rely on it. Raises
    ``ValueError``: a bad kind is a programming error that must surface at
    development time.
    """
    segments = kind.split(".")
    if (
        not KIND_PATTERN.match(kind)
        or segments[1] == "summary"
        or (len(segments) == 3 and segments[2] != "summary")
    ):
        raise ValueError(
            f"invalid record kind {kind!r}: expected '<plugin>.<noun>' with the plugin's "
            "name verbatim and a snake_case noun, optionally followed by '.summary', e.g. "
            "'github.code_hit', 'acme-tools.widget' or 'awx.apply_outcome.summary'"
        )


def _absolute(value: Path) -> Path:
    if not value.is_absolute():
        raise ValueError(f"target_path must be absolute, got {str(value)!r}")
    return value


AbsolutePath = Annotated[Path, AfterValidator(_absolute)]
"""A ``Path`` that must be absolute (validated, not resolved)."""


OUTCOME_ACTIONS: Final = frozenset(
    {
        "planned",
        "created",
        "updated",
        "deleted",
        "unchanged",
        "skipped",
        "failed",
        "partial",
        "conflict",
        "cancelled",
    }
)
"""The shared outcome vocabulary for ``action``.

A plugin may add past-tense verbs specific to its domain (``cloned``,
``pulled``, ``removed``); ``skipped`` is never a failure."""

FAILURE_ACTIONS: Final = frozenset({"failed", "partial", "conflict"})
"""Outcome actions that make a command exit ``1``."""

CheckStatus = Literal["pass", "warn", "fail", "error"]
"""The check vocabulary for ``status``."""


@dataclass(frozen=True)
class TableGlyph:
    """How a table shows a field's ``True``, ``False`` or unset (``None``) value.

    Annotate the field, as in ``Annotated[bool, TableGlyph(true="✓")]`` or
    ``Annotated[str | None, TableGlyph(none="—")]``. Only tables use it; every
    other format keeps the native value. A value without a glyph renders as
    usual.
    """

    true: str | None = None
    false: str | None = None
    none: str | None = None

    def show(self, value: object) -> object:
        """The glyph for ``value``, or ``value`` itself when it has none."""
        if value is None:
            glyph = self.none
        elif value is True:
            glyph = self.true
        elif value is False:
            glyph = self.false
        else:
            return value
        return value if glyph is None else glyph


def table_columns_of(model: type[BaseModel]) -> tuple[str, ...]:
    """The columns a table of ``model`` records shows by default (none: every field)."""
    return tuple(getattr(model, "table_columns", ()))


@cache
def _field_order(model: type[BaseModel]) -> tuple[str, ...]:
    """Field names by declaring class, most derived first.

    Pydantic lists inherited fields first; records want their own fields
    (including re-declared base fields) ahead of the base-class ones. An
    inherited ``action`` follows the identifying field (``id`` and ``name``
    when a record leads with both).
    """
    order: dict[str, None] = {}
    declared_by: dict[str, type] = {}
    for klass in model.__mro__:
        own = annotationlib.get_annotations(klass, format=annotationlib.Format.FORWARDREF)
        for name in own:
            if name in model.model_fields:
                order.setdefault(name)
                declared_by.setdefault(name, klass)
    names = list(order)
    if declared_by.get("action") is OutcomeRecord and declared_by[names[0]] not in _BASES:
        # An outcome's ``action`` follows the field(s) identifying the row.
        names.remove("action")
        names.insert(2 if names[:2] == ["id", "name"] else 1, "action")
    return tuple(names)


class DuplicateKindError(ValueError):
    """Two record models declare the same kind (reason ``duplicate-kind``)."""


_MODELS: dict[str, type[Record]] = {}
_KINDS: dict[type[BaseModel], str] = {}


def kind_of(model: type[BaseModel]) -> str | None:
    """The kind ``model`` declares (``None``: a kind-less model, such as the record bases)."""
    return _KINDS.get(model)


def record_model(kind: str) -> type[Record] | None:
    """The record model that declares ``kind`` (``None``: no model declares it)."""
    return _MODELS.get(kind)


def record_kinds() -> Mapping[str, type[Record]]:
    """Every declared kind and its model, read-only."""
    return MappingProxyType(_MODELS)


def _register(kind: str, model: type[Record]) -> None:
    """Map ``kind`` to ``model``; another model already declaring it is ``duplicate-kind``.

    The same class defined again (a module imported twice) replaces itself.
    """
    held = _MODELS.get(kind)
    if held is not None and (held.__module__, held.__qualname__) != (
        model.__module__,
        model.__qualname__,
    ):
        raise DuplicateKindError(
            f"duplicate-kind: {kind!r} is declared by both "
            f"{held.__module__}.{held.__qualname__} and {model.__module__}.{model.__qualname__}; "
            "each kind has exactly one schema, so give one of them another kind"
        )
    if held is not None:
        _KINDS.pop(held, None)
    _MODELS[kind] = model
    _KINDS[model] = kind


def _lossy(where: str, why: str) -> TypeError:
    return TypeError(
        f"{where}: {why}. A record's fields must survive a JSON round trip "
        "(pipes, state.yml and caches read records back), so this record would lose data"
    )


def _check_round_trip(
    model: type[BaseModel], seen: set[type[BaseModel]], where: str | None = None
) -> None:
    """Reject the field features that silently lose data in a JSON round trip.

    Covers ``model`` and every model nested in its fields: secrets
    (``SecretStr``, ``SecretBytes``), ``Field(exclude=True)``, an
    ``exclude_if`` that can drop a value other than the default, a
    serialization alias validation does not accept, and ``field_serializer``
    or ``PlainSerializer``/``WrapSerializer`` other than core's own. ``where``
    names the field a nested model sits in.
    """
    if model in seen:
        return
    seen.add(model)
    prefix = where or model.__qualname__
    if serializers := model.__pydantic_decorators__.field_serializers:
        fields = sorted({field for each in serializers.values() for field in each.info.fields})
        raise _lossy(prefix, f"field_serializer on {', '.join(fields)} may not invert")
    config = model.model_config
    by_name = bool(config.get("validate_by_name") or config.get("populate_by_name"))
    for name, info in model.model_fields.items():
        field = f"{prefix}.{name}"
        _check_field(field, name, info, by_name=by_name)
        for meta in info.metadata:
            _check_serializer(field, meta)
        _check_type(field, info.annotation, seen)


def _check_field(where: str, name: str, info: FieldInfo, *, by_name: bool) -> None:
    if info.exclude is True:
        raise _lossy(where, "Field(exclude=True) leaves it out of every dump")
    if info.exclude_if is not None and (
        info.is_required() or not info.exclude_if(info.get_default(call_default_factory=True))
    ):
        # Only "omit when it is the default" reads back to the same value.
        raise _lossy(where, "exclude_if must only drop the field's default")
    dumped_as = info.serialization_alias or name
    if dumped_as not in _accepted_keys(name, info, by_name=by_name):
        raise _lossy(where, f"dumps as {dumped_as!r}, which validation does not accept")


def _accepted_keys(name: str, info: FieldInfo, *, by_name: bool) -> set[str]:
    alias = info.validation_alias
    if alias is None:
        return {name}
    accepted = {name} if by_name else set()
    if isinstance(alias, str):
        return accepted | {alias}
    choices = getattr(alias, "choices", ())
    return accepted | {choice for choice in choices if isinstance(choice, str)}


def _check_serializer(where: str, meta: object) -> None:
    if isinstance(meta, PlainSerializer | WrapSerializer) and (
        meta.func not in _ROUND_TRIP_SERIALIZERS
    ):
        raise _lossy(where, f"{type(meta).__name__} {meta.func.__name__} may not invert")


def _check_type(where: str, annotation: object, seen: set[type[BaseModel]]) -> None:
    if isinstance(annotation, TypeAliasType):
        _check_type(where, annotation.__value__, seen)
        return
    origin = get_origin(annotation)
    if origin is Literal:
        return
    if origin is Annotated:
        for meta in getattr(annotation, "__metadata__", ()):
            _check_serializer(where, meta)
    if isinstance(annotation, type):
        if issubclass(annotation, SecretStr | SecretBytes):
            raise _lossy(where, f"{annotation.__name__} dumps masked")
        if issubclass(annotation, BaseModel):
            _check_round_trip(annotation, seen, where)
            return
    for arg in get_args(annotation):
        _check_type(where, arg, seen)


class Record(BaseModel):
    """Base for emitted records: frozen, with unknown fields rejected.

    Dumps list the record's own fields before inherited base fields, except
    that an inherited ``action`` follows the identifying field.

    A subclass declares its kind with a class keyword,
    ``class GitlabProject(Record, kind="gitlab.project")``, checked against
    the kind grammar (:func:`check_data_kind`) and registered kind → model;
    a second model declaring the same kind raises :class:`DuplicateKindError`.
    A subclass without ``kind=`` is kind-less, not its parent's kind. Every
    subclass is checked for fields that would lose data in a JSON round trip
    (``SecretStr``, ``Field(exclude=True)``, serialization-only aliases,
    custom serializers); the class definition raises ``TypeError`` naming
    the field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The fields a table of these records shows by default (empty: every field).
    table_columns: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, *, kind: str | None = None, **kwargs: Any) -> None:
        if kind is not None:
            check_data_kind(kind)
        super().__init_subclass__(**kwargs)

    @classmethod
    def __pydantic_init_subclass__(cls, *, kind: str | None = None, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        _check_round_trip(cls, set())
        if kind is not None:
            _register(kind, cls)

    @model_serializer(mode="wrap")
    def _own_fields_first(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if not isinstance(data, dict):
            return data
        ordered = {name: data[name] for name in _field_order(type(self)) if name in data}
        return ordered | data


def _is_none(value: object) -> bool:
    return value is None


#: The optional ``error`` of a row; omitted from output when the row did not fail.
_RowError = Annotated[ErrorInfo | None, Field(exclude_if=_is_none)]


class OutcomeRecord(Record):
    """A mutation result. Emit under the kind ``<cap>.<verb>_outcome``.

    A failed row may carry ``error`` (:class:`ErrorInfo`); it is left out of
    the output of every other row.
    """

    action: str
    error: _RowError = None

    @property
    def failed(self) -> bool:
        """Whether this outcome counts as a failure for the exit code."""
        return self.action in FAILURE_ACTIONS


class TargetRecord(Record):
    """A record about a filesystem target; ``target_path`` is absolute.

    A failed row may carry ``error`` (:class:`ErrorInfo`), as on
    :class:`OutcomeRecord`.
    """

    target_path: AbsolutePath
    error: _RowError = None


#: Record bases whose fields never identify a row.
_BASES: Final = (OutcomeRecord, TargetRecord)


class CheckRecord(Record):
    """One check result (doctor checks, validations)."""

    status: CheckStatus


__all__ = [
    "FAILURE_ACTIONS",
    "KIND_PATTERN",
    "OUTCOME_ACTIONS",
    "TABLE_CONTEXT",
    "AbsolutePath",
    "CheckRecord",
    "CheckStatus",
    "DuplicateKindError",
    "OutcomeRecord",
    "Record",
    "TableGlyph",
    "TargetRecord",
    "UtcTimestamp",
    "check_data_kind",
    "format_utc",
    "kind_of",
    "record_kinds",
    "record_model",
    "table_columns_of",
]
