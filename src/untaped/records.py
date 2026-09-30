"""Base models for the records commands emit, plus the timestamp type.

Capability output models subclass these bases so every kind shares one shape
for the fields the pipe contract fixes (``docs/conventions.md``):

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
  RFC 3339 with a ``Z`` suffix (``2026-01-02T03:04:05Z``);
- :class:`TableGlyph` — a field annotation that shows a value as a glyph in
  tables only.

A record type picks the columns a table of it shows by default with a
``table_columns`` class variable (:func:`table_columns_of`).

Records are frozen pydantic models; subclasses add their own fields, which
serialize before the base fields they inherit (the identifying field stays
first for ``--format raw`` and the first table column), except that an
inherited ``action`` follows the identifying field.
"""

from __future__ import annotations

import annotationlib
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from untaped.diagnostics import ErrorInfo


def _to_utc(value: datetime) -> datetime:
    """Normalize to an aware UTC datetime (naive values are taken as UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Render ``value`` as RFC 3339 UTC with a ``Z`` suffix, to the second."""
    return _to_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


UtcTimestamp = Annotated[
    datetime,
    AfterValidator(_to_utc),
    PlainSerializer(format_utc, return_type=str, when_used="json"),
]
"""A UTC ``datetime`` field that renders as ``2026-01-02T03:04:05Z`` in output.

Name such fields ``<event>_at`` (``created_at``, ``scanned_at``)."""


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

A capability may add past-tense verbs specific to its domain (``cloned``,
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


class Record(BaseModel):
    """Base for emitted records: frozen, with unknown fields rejected.

    Dumps list the record's own fields before inherited base fields, except
    that an inherited ``action`` follows the identifying field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The fields a table of these records shows by default (empty: every field).
    table_columns: ClassVar[tuple[str, ...]] = ()

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
    "OUTCOME_ACTIONS",
    "AbsolutePath",
    "CheckRecord",
    "CheckStatus",
    "OutcomeRecord",
    "Record",
    "TableGlyph",
    "TargetRecord",
    "UtcTimestamp",
    "format_utc",
    "table_columns_of",
]
