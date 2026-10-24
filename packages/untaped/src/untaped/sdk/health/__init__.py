"""Health and moves (``untaped.sdk.health``): doctor checks, setup marks, renamed keys."""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.sdk._lazy import lazy_names

if TYPE_CHECKING:
    from untaped.sdk.health.deprecated_keys import Renamed, Retired
    from untaped.sdk.health.doctor import (
        DoctorCheck,
        DoctorResult,
        ExecutableCheck,
        OnlineCheck,
        PluginContext,
    )
    from untaped.sdk.health.marks import NeedsSetup, Suggest
    from untaped.sdk.health.migrations import DirMigration

__all__ = [
    "DirMigration",
    "DoctorCheck",
    "DoctorResult",
    "ExecutableCheck",
    "NeedsSetup",
    "OnlineCheck",
    "PluginContext",
    "Renamed",
    "Retired",
    "Suggest",
]

__getattr__, __dir__ = lazy_names(
    globals(),
    {
        "deprecated_keys": ("Renamed", "Retired"),
        "doctor": (
            "DoctorCheck",
            "DoctorResult",
            "ExecutableCheck",
            "OnlineCheck",
            "PluginContext",
        ),
        "marks": ("NeedsSetup", "Suggest"),
        "migrations": ("DirMigration",),
    },
)
