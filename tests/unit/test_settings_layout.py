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
from untaped.settings_layout import FlatSettingsLayout, reset_flat_layout_warning_for_tests


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    reset_config_registry_for_tests()
    reset_flat_layout_warning_for_tests()
    yield
    reset_config_registry_for_tests()
    reset_flat_layout_warning_for_tests()


class TestFlatSettingsLayout:
    def test_effective_returns_top_level_keys(self) -> None:
        layout = FlatSettingsLayout()
        raw = {"log_level": "DEBUG", "http": {"verify_ssl": False}}
        assert layout.effective(raw) == raw

    def test_effective_drops_profile_keys(self, capsys: pytest.CaptureFixture[str]) -> None:
        layout = FlatSettingsLayout()
        raw = {"log_level": "DEBUG", "profiles": {"prod": {}}, "active": "prod"}
        assert layout.effective(raw) == {"log_level": "DEBUG"}

    def test_warns_once_when_profile_keys_present(self, capsys: pytest.CaptureFixture[str]) -> None:
        layout = FlatSettingsLayout()
        layout.effective({"profiles": {"prod": {}}})
        layout.effective({"profiles": {"prod": {}}})
        err = capsys.readouterr().err
        assert err.count("warning: config defines profiles") == 1
        assert "ignored" in err
        assert "untaped plugins add untaped-profile" in err

    def test_no_warning_for_flat_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        FlatSettingsLayout().effective({"log_level": "DEBUG"})
        assert capsys.readouterr().err == ""

    def test_scopes_are_unsupported(self) -> None:
        layout = FlatSettingsLayout()
        raw = {"log_level": "DEBUG"}
        assert layout.supports_scopes is False
        assert layout.scope_names(raw) == []
        assert layout.scope_data(raw, "prod") is None

    def test_provenance_maps_every_effective_leaf_to_config(self) -> None:
        layout = FlatSettingsLayout()
        raw = {
            "log_level": "DEBUG",
            "http": {"verify_ssl": False},
            "profiles": {"prod": {"log_level": "ERROR"}},
        }
        assert layout.provenance(raw) == {
            ("log_level",): "config",
            ("http", "verify_ssl"): "config",
        }

    def test_provenance_is_empty_for_empty_config(self) -> None:
        assert FlatSettingsLayout().provenance({}) == {}

    def test_write_scope_targets_top_level(self) -> None:
        layout = FlatSettingsLayout()
        raw: dict[str, Any] = {"log_level": "DEBUG"}
        target, name = layout.write_scope(raw, None)
        assert target is raw
        assert name is None

    def test_write_scope_rejects_requested_scope(self) -> None:
        layout = FlatSettingsLayout()
        with pytest.raises(ConfigError, match="profiles are not available") as excinfo:
            layout.write_scope({}, "prod")
        assert "prod" in str(excinfo.value)
        assert "untaped-profile" in str(excinfo.value)


class TestDefaultLayout:
    def test_active_layout_is_flat_by_default(self) -> None:
        assert isinstance(active_settings_layout(), FlatSettingsLayout)

    def test_flat_top_level_keys_are_effective_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no registered layout, top-level keys ARE the settings."""
        cfg = tmp_path / "config.yml"
        cfg.write_text("log_level: DEBUG\n")
        monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
        get_settings.cache_clear()

        assert get_settings().log_level == "DEBUG"

    def test_profile_shaped_config_is_ignored_with_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cfg = tmp_path / "config.yml"
        cfg.write_text("profiles:\n  default:\n    log_level: DEBUG\nactive: default\n")
        monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
        get_settings.cache_clear()

        assert get_settings().log_level == "INFO"
        assert "warning: config defines profiles" in capsys.readouterr().err


class TestRegisteredLayout:
    def test_registered_provider_wins(self) -> None:
        flat = FlatSettingsLayout()
        register_settings_layout(lambda: flat)
        assert active_settings_layout() is flat

    def test_provider_is_resolved_lazily_and_once(self) -> None:
        calls = 0

        def provider() -> FlatSettingsLayout:
            nonlocal calls
            calls += 1
            return FlatSettingsLayout()

        register_settings_layout(provider)
        assert calls == 0
        first = active_settings_layout()
        second = active_settings_layout()
        assert first is second
        assert calls == 1

    def test_duplicate_registration_is_rejected(self) -> None:
        register_settings_layout(FlatSettingsLayout)
        with pytest.raises(ConfigError, match="settings layout"):
            register_settings_layout(FlatSettingsLayout)

    def test_different_key_registration_is_rejected(self) -> None:
        register_settings_layout(FlatSettingsLayout, key="pkg.a:LAYOUT")
        with pytest.raises(ConfigError, match="settings layout"):
            register_settings_layout(FlatSettingsLayout, key="pkg.b:LAYOUT")

    def test_same_key_reregistration_refreshes_provider(self) -> None:
        first = FlatSettingsLayout()
        second = FlatSettingsLayout()
        register_settings_layout(lambda: first, key="pkg.a:LAYOUT")
        assert active_settings_layout() is first
        register_settings_layout(lambda: second, key="pkg.a:LAYOUT")
        assert active_settings_layout() is second

    def test_get_settings_reads_through_registered_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "config.yml"
        cfg.write_text("log_level: DEBUG\n")
        monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
        register_settings_layout(FlatSettingsLayout)
        get_settings.cache_clear()

        assert get_settings().log_level == "DEBUG"
