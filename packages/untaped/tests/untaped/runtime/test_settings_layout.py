"""Tests for the SDK's single, always-on settings layout.

There is no registry: ``active_settings_layout()`` always returns the
built-in :class:`ProfilesSettingsLayout`. These tests pin that the default
layout resolves profile-shaped config and falls back to schema defaults for
bare top-level keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.runtime.settings import (
    active_settings_layout,
    get_settings,
)
from untaped.runtime.settings_layout import ProfilesSettingsLayout


class TestDefaultLayout:
    def test_active_layout_is_profiles_by_default(self) -> None:
        assert isinstance(active_settings_layout(), ProfilesSettingsLayout)

    def test_profile_shaped_config_resolves_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The profiles layout resolves the base, config.yml's top level."""
        cfg = tmp_path / "config.yml"
        cfg.write_text("format_version: 2\nuntaped:\n  skills:\n    updates: auto\n")
        monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
        get_settings.cache_clear()

        assert get_settings().untaped.skills.updates == "auto"

    def test_a_named_profile_is_read_only_when_picked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "config.yml"
        cfg.write_text(
            "format_version: 2\nprofiles:\n  work:\n    untaped:\n      skills:\n"
            "        updates: warn\n"
        )
        monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
        get_settings.cache_clear()
        assert get_settings().untaped.skills.updates == "auto"  # the default

        monkeypatch.setenv("UNTAPED_PROFILE", "work")
        get_settings.cache_clear()
        assert get_settings().untaped.skills.updates == "warn"

    def test_active_layout_is_a_singleton(self) -> None:
        """There is one layout instance; repeated calls return the same object."""
        assert active_settings_layout() is active_settings_layout()
