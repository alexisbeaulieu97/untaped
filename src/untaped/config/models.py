"""Models and display helpers for the root config command group."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr
from pydantic_core import to_jsonable_python

from untaped.config_schema import (
    FieldDescriptor,
    redact_nested_url_passwords,
    redact_url_password,
)


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


UNSET_GLYPH = "—"
"""Human-output placeholder for an unset value (table/raw only)."""


def setting_entry_row(entry: SettingEntry, *, human: bool) -> dict[str, object]:
    """Render a setting entry as the config list/get row contract.

    ``human`` (table/raw output) renders display text — ``—`` for unset
    values, ``""`` for no profile. Structured output keeps native values
    (``null``, booleans, numbers).
    """
    return {
        "key": entry.key,
        "value": _human(entry.value) if human else entry.value,
        "default": _human(entry.default) if human else entry.default,
        "source": entry.source.label,
        "profile": (entry.profile or "") if human else entry.profile,
    }


def _human(value: object) -> str:
    if value is None:
        return UNSET_GLYPH
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
