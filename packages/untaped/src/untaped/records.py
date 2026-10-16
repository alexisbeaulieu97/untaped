"""Base models for the records commands emit, plus the timestamp type.

Plugin output models subclass these bases so every kind shares one shape
for the fields the pipe contract fixes (``docs/reference/conventions.md#output-records``):

- :class:`OutcomeRecord` — a mutation result with an ``action`` from the
  outcome vocabulary (``created``, ``updated``, ``failed``, ...);
- :class:`TargetRecord` — a record about a filesystem target, carrying an
  absolute ``target_path``;
- :class:`CheckRecord` — a check result with a ``status`` from the check
  vocabulary (``pass``/``warn``/``fail``/``error``);
- ``error`` — the optional :class:`~untaped.output.diagnostic.Failure` of a
  failed outcome or target row (``category``, ``system``, ``retryable``,
  ``exit_code``, ``details``, ``message``, ``hint``);
- :data:`UtcTimestamp` — a ``datetime`` normalized to UTC that serializes as
  RFC 3339 with a ``Z`` suffix (``2026-01-02T03:04:05Z``), shown to the
  second in tables;
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

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Annotated,
    Any,
    ClassVar,
    Final,
    Literal,
)

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from untaped.output.diagnostic import (
    DuplicateKindError,
    Failure,
    ForeignKindError,
    KindError,
    UtcTimestamp,
    WireModel,
    _is_none,
    check_data_kind,
    format_utc,
    kind_of,
    record_kinds,
    record_model,
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


class Record(WireModel):
    """Base for emitted records: frozen, with unknown fields rejected.

    Dumps list the record's own fields before inherited base fields, except
    that an inherited ``action`` follows the identifying field.

    A subclass declares its kind with a class keyword,
    ``class GitlabProject(Record, kind="gitlab.project")``, checked against
    the kind grammar (:func:`check_data_kind`) and registered kind → model;
    a second model declaring the same kind raises :class:`DuplicateKindError`.
    A subclass without ``kind=`` is kind-less, not its parent's kind. Every
    subclass, with every model nested in it, is checked for what would lose
    data in a JSON round trip of the dump ``emit`` writes: secrets
    (``SecretStr``, ``Secret``), ``Field(exclude=True)``, an ``exclude_if``
    other than core's omit-when-``None``, an alias validation does not accept,
    computed fields, custom serializers and forward references not yet
    defined. The class definition raises ``TypeError`` naming the field.
    """

    #: The fields a table of these records shows by default (empty: every field).
    table_columns: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def _reorder(cls, names: list[str], declared_by: Mapping[str, type]) -> list[str]:
        """An inherited outcome ``action`` follows the field(s) identifying the row."""
        if declared_by.get("action") is OutcomeRecord and declared_by[names[0]] not in _BASES:
            names.remove("action")
            names.insert(2 if names[:2] == ["id", "name"] else 1, "action")
        return names


#: The optional ``error`` of a row; omitted from output when the row did not fail.
_RowError = Annotated[Failure | None, Field(exclude_if=_is_none)]


class OutcomeRecord(Record):
    """A mutation result; declare its kind as ``<plugin>.<verb>_outcome``.

    A failed row may carry ``error`` (a :class:`~untaped.output.diagnostic.Failure`);
    it is left out of the output of every other row.
    """

    action: str
    error: _RowError = None

    @property
    def failed(self) -> bool:
        """Whether this outcome counts as a failure for the exit code."""
        return self.action in FAILURE_ACTIONS


class TargetRecord(Record):
    """A record about a filesystem target; ``target_path`` is absolute.

    A failed row may carry ``error`` (a ``Failure``), as on
    :class:`OutcomeRecord`.
    """

    target_path: AbsolutePath
    error: _RowError = None


#: Record bases whose fields never identify a row.
_BASES: Final = (OutcomeRecord, TargetRecord)


class CheckRecord(Record):
    """One check result (doctor checks, validations)."""

    status: CheckStatus


class Source(BaseModel):
    """Where an issued record came from: the provider's plugin and its own record.

    ``record`` is the JSON dump of the provider's own record when its item type
    is not the owner's model, else empty. The SDK fills it; a provider never does.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plugin: str
    kind: str
    record: dict[str, Any] = {}


class Issued(Record):
    """A record a provider issues on an owner's behalf; ``source`` says which provider.

    Exported from ``untaped.contracts``; it lives here so an owner's model
    (kept in ``state.yml``, say) loads without the contracts machinery.
    """

    source: Source | None = None


__all__ = [
    "FAILURE_ACTIONS",
    "OUTCOME_ACTIONS",
    "AbsolutePath",
    "CheckRecord",
    "CheckStatus",
    "DuplicateKindError",
    "ForeignKindError",
    "Issued",
    "KindError",
    "OutcomeRecord",
    "Record",
    "Source",
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
