"""Domain entities for the config bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
