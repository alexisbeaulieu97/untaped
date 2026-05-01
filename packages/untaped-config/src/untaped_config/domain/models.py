"""Domain entities for the config bounded context."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator


class Source(BaseModel):
    """Where a setting's effective value is coming from.

    Resolution chain (high → low priority):

    - ``env``     — an ``UNTAPED_*`` environment variable.
    - ``profile`` — a YAML profile (``profile`` field names which one).
    - ``default`` — the schema default declared on the Pydantic model.
    - ``unset``   — no default, no value; the field is genuinely empty.

    JSON output keeps the structured shape (``{"kind": "profile",
    "profile": "prod"}``); raw / table output uses :pyattr:`label` so the
    same column reads as ``profile:prod`` in pipes and tables.
    """

    kind: Literal["profile", "env", "default", "unset"]
    profile: str | None = None

    @model_validator(mode="after")
    def _profile_required_for_profile_kind(self) -> Source:
        if self.kind == "profile" and not self.profile:
            raise ValueError("profile field is required when kind == 'profile'")
        if self.kind != "profile" and self.profile is not None:
            raise ValueError("profile field must be None unless kind == 'profile'")
        return self

    @property
    def label(self) -> str:
        """Human-readable single-line form (used in tables and raw output)."""
        if self.kind == "profile":
            return f"profile:{self.profile}"
        return self.kind

    def __str__(self) -> str:
        return self.label


class SettingEntry(BaseModel):
    """One row in the ``untaped config list`` table.

    Secret values are pre-masked into ``value`` (``"***"``), so callers don't
    need a separate ``is_secret`` flag.
    """

    key: str
    value: str
    default: str
    source: Source
    profile: str | None = None
    """Set in ``--all-profiles`` mode to name the profile owning this row."""
