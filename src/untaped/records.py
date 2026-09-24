"""Base models for the records commands emit, plus the timestamp type.

Capability output models subclass these bases so every kind shares one shape
for the fields the pipe contract fixes (``docs/conventions.md``):

- :class:`OutcomeRecord` — a mutation result with an ``action`` from the
  outcome vocabulary (``created``, ``updated``, ``failed``, ...);
- :class:`TargetRecord` — a record about a filesystem target, carrying an
  absolute ``target_path``;
- :class:`CheckRecord` — a check result with a ``status`` from the check
  vocabulary (``pass``/``warn``/``fail``/``error``);
- :data:`UtcTimestamp` — a ``datetime`` normalized to UTC that serializes as
  RFC 3339 with a ``Z`` suffix (``2026-01-02T03:04:05Z``).

Records are frozen pydantic models; subclasses add their own fields, which
serialize before the base fields they inherit (the identifying field stays
first for ``--format raw`` and the first table column).
"""

from __future__ import annotations

import annotationlib
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    PlainSerializer,
    SerializerFunctionWrapHandler,
    model_serializer,
)


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


@cache
def _field_order(model: type[BaseModel]) -> tuple[str, ...]:
    """Field names by declaring class, most derived first.

    Pydantic lists inherited fields first; records want their own fields
    (including re-declared base fields) ahead of the base-class ones.
    """
    order: dict[str, None] = {}
    for klass in model.__mro__:
        own = annotationlib.get_annotations(klass, format=annotationlib.Format.FORWARDREF)
        order.update(dict.fromkeys(name for name in own if name in model.model_fields))
    return tuple(order)


class Record(BaseModel):
    """Base for emitted records: frozen, with unknown fields rejected.

    Dumps list the record's own fields before inherited base fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_serializer(mode="wrap")
    def _own_fields_first(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if not isinstance(data, dict):
            return data
        ordered = {name: data[name] for name in _field_order(type(self)) if name in data}
        return ordered | data


class OutcomeRecord(Record):
    """A mutation result. Emit under the kind ``<cap>.<verb>_outcome``."""

    action: str

    @property
    def failed(self) -> bool:
        """Whether this outcome counts as a failure for the exit code."""
        return self.action in FAILURE_ACTIONS


class TargetRecord(Record):
    """A record about a filesystem target; ``target_path`` is absolute."""

    target_path: AbsolutePath


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
    "TargetRecord",
    "UtcTimestamp",
    "format_utc",
]
