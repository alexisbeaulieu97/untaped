"""Models and display helpers for the per-tool config command group."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr

from untaped.config_schema import FieldDescriptor


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
    """One row in the ``<tool> config list`` table.

    Secret values are pre-masked into ``value`` (``"***"``), so callers don't
    need a separate ``is_secret`` flag.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    key: str
    value: str
    default: str
    source: Source
    profile: str | None = None
    """Set in ``--all-profiles`` mode to name the profile owning this row."""


def setting_entry_row(entry: SettingEntry) -> dict[str, object]:
    """Render a setting entry as the config list/get row contract."""
    return {
        "key": entry.key,
        "value": entry.value,
        "default": entry.default,
        "source": entry.source.label,
        "profile": entry.profile or "",
    }


def display_value(descriptor: FieldDescriptor, value: Any, *, reveal_secrets: bool) -> str:
    """Format a setting value for table display."""
    if value is None:
        return "—"
    if descriptor.is_secret and not reveal_secrets:
        return "***"
    if isinstance(value, SecretStr):
        return value.get_secret_value() if reveal_secrets else "***"
    return str(value)


def display_default(descriptor: FieldDescriptor) -> str:
    if not descriptor.has_default or descriptor.default is None:
        return "—"
    if isinstance(descriptor.default, SecretStr):
        return descriptor.default.get_secret_value()
    return str(descriptor.default)
