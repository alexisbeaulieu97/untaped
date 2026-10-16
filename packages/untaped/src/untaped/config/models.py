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
from untaped.messages import removal_phrase
from untaped.records import OutcomeRecord, Record, TableGlyph
from untaped.redaction import redact_url_password
from untaped.stability import SettingStability, removal_text


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
    variable: str | None = None
    """For ``env``: the variable's name (``UNTAPED_AWX__PAGE_SIZE``). ``label`` stays ``env``."""

    @property
    def label(self) -> str:
        """Human-readable single-line form (e.g. ``profile:prod``)."""
        return f"profile:{self.profile}" if self.kind == "profile" else self.kind

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class OldSpelling:
    """An old key or variable a setting's value was read through, and when it stops being read."""

    shown: str
    """As the user wrote it: ``ansible.stale_after_seconds`` or ``UNTAPED_ANSIBLE__…``."""
    removed_in: int | None
    section: str
    """The section it belongs to, whose plugin's major ``removed_in`` is."""


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
    """Why the value deserves a look, e.g. ``from deprecated demo.old_key``."""
    stability: SettingStability = "stable"
    """The setting's own mark, else its plugin's."""
    use: str | None = None
    """For a deprecated setting: what replaces it, as help shows it."""
    removed_in: int | None = None
    """For a deprecated setting: the major that removes it."""
    distribution: str | None = None
    """For a deprecated setting: the plugin distribution whose major that is (``None``: core)."""


class SettingOutcome(OutcomeRecord, kind="untaped.setting_outcome"):
    """The result of ``config set``/``unset``.

    ``action`` is ``updated`` (set), ``deleted`` or ``unchanged`` (unset), or
    ``planned`` under ``--dry-run``. The value is never echoed: it may be a
    secret.
    """

    key: str
    profile: str


_UNSET = TableGlyph(none="—")
"""Tables show an unset value as ``—``; every other format prints it natively."""


class SettingRow(Record, kind="untaped.setting"):
    """One row of ``config list``/``get``.

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
    """Set when the value came from a deprecated key or variable (``from OLD, removed
    in …``), and for a deprecated setting (``use NEW, removed in …``)."""
    stability: SettingStability = "stable"
    """``stable``, ``experimental`` or ``deprecated``; the table is split by it, so
    it is not a column."""
    removed_in: int | None = None
    """For a deprecated setting: the major that removes it (``None`` when undated)."""
    distribution: str | None = None
    """For a deprecated setting: the plugin distribution whose major that is; ``None``
    for a core setting."""


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
        note=entry.note or _deprecated_note(entry),
        stability=entry.stability,
        removed_in=entry.removed_in,
        distribution=entry.distribution,
    )


def _deprecated_note(entry: SettingEntry) -> str | None:
    """A deprecated setting's note: ``use NEW, removed in …`` (``removed in …`` without one)."""
    if entry.stability != "deprecated":
        return None
    removal = removal_phrase(removal_text(entry.removed_in, entry.distribution))
    return removal if entry.use is None else f"use {entry.use}, {removal}"


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
