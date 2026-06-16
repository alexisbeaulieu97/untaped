"""Tests for the SDK's single, always-on settings layout.

There is no registry: ``active_settings_layout()`` always returns the
built-in :class:`ProfilesSettingsLayout`. These tests pin that the default
layout resolves profile-shaped config and falls back to schema defaults for
bare top-level keys.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from untaped.settings import (
    active_settings_layout,
    get_settings,
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

    def test_active_layout_is_a_singleton(self) -> None:
        """There is one layout instance; repeated calls return the same object."""
        assert active_settings_layout() is active_settings_layout()
