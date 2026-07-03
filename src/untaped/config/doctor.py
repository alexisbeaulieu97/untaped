"""Diagnostics for the ``<tool> config doctor`` command."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from untaped.cli import emit, report_errors
from untaped.config.models import setting_entry_row
from untaped.config.ports import SettingsRepository
from untaped.config.use_cases import ListSettings
from untaped.config_file import read_config_dict
from untaped.profile_resolver import classify_active_profile
from untaped.settings import get_settings, legacy_flat_sections, resolve_config_path
from untaped.ui import UiContext, ui_context


def warn_legacy_flat(ui: UiContext, raw: Mapping[str, Any]) -> None:
    """Warn about each top-level section the v2 profiles layout silently ignores."""
    for section in legacy_flat_sections(raw):
        ui.message(
            "warning",
            f"top-level `{section}:` is ignored since the v2 profiles layout — "
            f"move it under `profiles.default.{section}`",
        )


def run_config_doctor(repo: SettingsRepository) -> None:
    """Diagnose config layout, active profile, and resolved settings."""
    with report_errors():
        ui = ui_context(strict=False)
        path = resolve_config_path()
        ui.message("info", f"config: {path}")
        raw = read_config_dict(path)
        name, source = classify_active_profile(raw)
        ui.message("info", f"active profile: {name or 'default'} (source: {source})")
        profiles = repo.profile_names()
        if profiles:
            ui.message("info", f"profiles: {', '.join(profiles)}")
        warn_legacy_flat(ui, raw)
        get_settings.cache_clear()
        get_settings()  # raises ConfigError (-> clean error, exit 1) if invalid
        ui.message("success", "config loaded OK")
        emit([setting_entry_row(e) for e in ListSettings(repo)()], fmt="table")
