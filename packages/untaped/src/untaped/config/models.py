"""Models and display helpers for the root config command group."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, SecretStr
from pydantic_core import to_jsonable_python

from untaped.config_schema import (
    FieldDescriptor,
    redact_nested_url_passwords,
)
from untaped.records import OutcomeRecord, Record, TableGlyph
from untaped.redaction import redact_url_password
from untaped.stability import SettingStability


@dataclass(frozen=True)
class Source:
    """Where a setting's effective value is coming from.

    Resolution chain (high → low priority):

    - ``env``     — an ``UNTAPED_*`` environment variable.
    - ``profile`` — a YAML profile (``profile`` field names which one).
    - ``default`` — the schema default declared on the Pydantic model.
    - ``unset``   — no default, no value; the field is genuinely empty.
    """

    kind: Literal["profile", "env", "default", "unset"]
    profile: str | None = None

    @property
    def label(self) -> str:
        """Human-readable single-line form (e.g. ``profile:prod``)."""
        return f"profile:{self.profile}" if self.kind == "profile" else self.kind

    def __str__(self) -> str:
        return self.label


class SettingEntry(BaseModel):
    """One row in the ``untaped config list`` table.

    ``value``/``default`` are native JSON-compatible values (``None`` when
    unset). Secret values are pre-masked (``"***"``) unless revealed, so
    callers don't need a separate ``is_secret`` flag.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    key: str
    value: object
    default: object
    source: Source
    profile: str | None = None
    """Set in ``--all-profiles`` mode to name the profile owning this row."""
    note: str | None = None
    """Why the value deserves a look, e.g. ``from deprecated github.corpus_path``."""
    stability: SettingStability = "stable"
    """The setting's own mark, else its capability's."""
    use: str | None = None
    """For a deprecated setting: what replaces it, as help shows it."""


class SettingOutcome(OutcomeRecord):
    """The result of ``config set``/``unset`` (kind ``untaped.setting_outcome``).

    ``action`` is ``updated`` (set), ``deleted`` or ``unchanged`` (unset), or
    ``planned`` under ``--dry-run``. The value is never echoed: it may be a
    secret.
    """

    key: str
    profile: str


_UNSET = TableGlyph(none="—")
"""Tables show an unset value as ``—``; every other format prints it natively."""


class SettingRow(Record):
    """One row of ``config list``/``get`` (kind ``untaped.setting``).

    ``value``/``default`` are native (``None`` when unset, secrets already
    masked); table and raw output get a set value as its text, a mapping or
    list as compact JSON, which is also valid input for ``config set``.
    """

    table_columns: ClassVar[tuple[str, ...]] = (
        "key",
        "value",
        "default",
        "source",
        "profile",
        "note",
    )

    key: str
    value: Annotated[object, _UNSET]
    default: Annotated[object, _UNSET]
    source: str
    profile: str | None
    """Set in ``--all-profiles`` mode to name the profile owning this row."""
    note: str | None = None
    """Set when the value came from a deprecated key or variable, or the setting is
    deprecated and names a replacement."""
    stability: SettingStability = "stable"
    """``stable``, ``experimental`` or ``deprecated``; the table is split by it, so
    it is not a column."""


def setting_entry_row(entry: SettingEntry, *, human: bool) -> SettingRow:
    """Render a setting entry as the config list/get row contract.

    ``human`` (table/raw output) writes a set value verbatim as text, so a
    table never reformats it.
    """
    return SettingRow(
        key=entry.key,
        value=_human(entry.value) if human else entry.value,
        default=_human(entry.default) if human else entry.default,
        source=entry.source.label,
        profile=entry.profile,
        note=entry.note or (None if entry.use is None else f"use {entry.use}"),
        stability=entry.stability,
    )


def _human(value: object) -> str | None:
    if value is None:
        return None  # a table shows it as `—`, raw as nothing
    if isinstance(value, dict | list):
        # Compact JSON: readable, and valid input for ``config set``.
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def display_value(descriptor: FieldDescriptor, value: Any, *, reveal_secrets: bool) -> object:
    """Normalize a setting value for output: native, masked, JSON-compatible."""
    if value is None:
        return None
    if descriptor.is_secret and not reveal_secrets:
        return "***"
    if isinstance(value, SecretStr):
        return value.get_secret_value() if reveal_secrets else "***"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return value if reveal_secrets else redact_url_password(value)
    if isinstance(value, bool | int | float):
        return value
    if descriptor.is_collection:
        native = to_jsonable_python(value)
        return native if reveal_secrets else redact_nested_url_passwords(native)
    return str(value)


def display_default(descriptor: FieldDescriptor, *, reveal_secrets: bool = False) -> object:
    if not descriptor.has_default or descriptor.default is None:
        return None
    return display_value(descriptor, descriptor.default, reveal_secrets=reveal_secrets)
