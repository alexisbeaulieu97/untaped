"""Core's writer of a plugin's own settings: what ``PluginHandle.plan_settings`` reaches.

The runtime gives each run this planner (``Run.plan_settings``), so the SDK
reaches core's config writer without importing core.
"""

from __future__ import annotations

from collections.abc import Mapping

from untaped.core.config.repository import SettingsFileRepository
from untaped.core.config.use_cases import PlannedSettings


def plan_settings(section: str, changes: Mapping[tuple[str, ...], object]) -> PlannedSettings:
    """``section``'s changes, each by its path in the section, planned in the home's file."""
    keys = {".".join((section, *path)): value for path, value in changes.items()}
    return PlannedSettings(SettingsFileRepository, keys)


__all__ = ["plan_settings"]
