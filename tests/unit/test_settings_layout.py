"""Tests for the settings layout protocol and registry integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from untaped.errors import ConfigError
from untaped.settings import (
    active_settings_layout,
    get_settings,
    register_settings_layout,
    reset_config_registry_for_tests,
)
from untaped.settings_layout import ProfilesSettingsLayout


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    reset_config_registry_for_tests()
    yield
    reset_config_registry_for_tests()


class TestDefaultLayout:
    def test_active_layout_is_profiles_by_default(self) -> None:
        assert isinstance(active_settings_layout(), ProfilesSettingsLayout)

    def test_profile_shaped_config_resolves_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no tool-registered layout, the default profiles layout resolves profiles.default."""
        cfg = tmp_path / "config.yml"
        cfg.write_text("profiles:\n  default:\n    log_level: DEBUG\nactive: default\n")
        monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
        get_settings.cache_clear()

        assert get_settings().log_level == "DEBUG"

    def test_top_level_keys_outside_profiles_fall_to_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Profiles-first: a bare top-level key (no profile) is not effective."""
        cfg = tmp_path / "config.yml"
        cfg.write_text("log_level: DEBUG\n")
        monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
        get_settings.cache_clear()

        assert get_settings().log_level == "INFO"


class TestRegisteredLayout:
    def test_registered_provider_wins(self) -> None:
        layout = ProfilesSettingsLayout()
        register_settings_layout(lambda: layout)
        assert active_settings_layout() is layout

    def test_provider_is_resolved_lazily_and_once(self) -> None:
        calls = 0

        def provider() -> ProfilesSettingsLayout:
            nonlocal calls
            calls += 1
            return ProfilesSettingsLayout()

        register_settings_layout(provider)
        assert calls == 0
        first = active_settings_layout()
        second = active_settings_layout()
        assert first is second
        assert calls == 1

    def test_duplicate_registration_is_rejected(self) -> None:
        register_settings_layout(ProfilesSettingsLayout)
        with pytest.raises(ConfigError, match="settings layout"):
            register_settings_layout(ProfilesSettingsLayout)

    def test_different_key_registration_is_rejected(self) -> None:
        register_settings_layout(ProfilesSettingsLayout, key="pkg.a:LAYOUT")
        with pytest.raises(ConfigError, match="settings layout"):
            register_settings_layout(ProfilesSettingsLayout, key="pkg.b:LAYOUT")

    def test_same_key_reregistration_refreshes_provider(self) -> None:
        first = ProfilesSettingsLayout()
        second = ProfilesSettingsLayout()
        register_settings_layout(lambda: first, key="pkg.a:LAYOUT")
        assert active_settings_layout() is first
        register_settings_layout(lambda: second, key="pkg.a:LAYOUT")
        assert active_settings_layout() is second

    def test_get_settings_reads_through_registered_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "config.yml"
        cfg.write_text("profiles:\n  default:\n    log_level: DEBUG\nactive: default\n")
        monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
        register_settings_layout(ProfilesSettingsLayout)
        get_settings.cache_clear()

        assert get_settings().log_level == "DEBUG"
