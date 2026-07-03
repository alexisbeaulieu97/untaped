"""Per-tool ``config`` command group and settings read/write use cases.

Flat layout mirroring ``untaped.profile``: ``app`` (cyclopts wiring),
``models``, ``ports``, ``repository``, ``use_cases`` — plus the app's split
helpers ``doctor`` / ``editor`` / ``prompting``.
"""

from __future__ import annotations

from untaped.config.app import build_config_app
from untaped.config.models import SettingEntry, Source, display_default, display_value
from untaped.config.ports import SettingsReader, SettingsRepository
from untaped.config.repository import SettingsFileRepository
from untaped.config.use_cases import (
    GetSetting,
    ListAllProfilesSettings,
    ListSettings,
    SetSetting,
    SetSettingResult,
    ToolConfigContext,
    UnsetSetting,
    UnsetSettingResult,
)

__all__ = [
    "GetSetting",
    "ListAllProfilesSettings",
    "ListSettings",
    "SetSetting",
    "SetSettingResult",
    "SettingEntry",
    "SettingsFileRepository",
    "SettingsReader",
    "SettingsRepository",
    "Source",
    "ToolConfigContext",
    "UnsetSetting",
    "UnsetSettingResult",
    "build_config_app",
    "display_default",
    "display_value",
]
