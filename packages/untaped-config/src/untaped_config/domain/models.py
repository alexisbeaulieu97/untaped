"""Domain entities for the config bounded context."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Source = Literal["yaml", "env", "default", "unset"]
"""Where a setting's current effective value is coming from."""


class SettingEntry(BaseModel):
    """One row in the ``untaped config list`` table.

    Secret values are pre-masked into ``value`` (``"***"``), so callers don't
    need a separate ``is_secret`` flag.
    """

    key: str
    value: str
    default: str
    source: Source
