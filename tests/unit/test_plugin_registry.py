from __future__ import annotations

import pytest
import typer
from pydantic import BaseModel, SecretStr

from untaped.errors import ConfigError
from untaped.plugins import DiagnosticResult, PluginRegistry


class DemoSettings(BaseModel):
    token: SecretStr | None = None


class DemoState(BaseModel):
    entries: list[str] = []


def test_registry_rejects_duplicate_plugin_ids() -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("demo")

    with pytest.raises(ConfigError, match="duplicate plugin id"):
        registry.add_plugin_id("demo")


def test_registry_rejects_duplicate_cli_names() -> None:
    registry = PluginRegistry()
    registry.add_cli("demo", typer.Typer())

    with pytest.raises(ConfigError, match="duplicate CLI command"):
        registry.add_cli("demo", typer.Typer())


def test_registry_rejects_duplicate_profile_setting_sections() -> None:
    registry = PluginRegistry()
    registry.add_profile_settings("demo", DemoSettings)

    with pytest.raises(ConfigError, match="duplicate profile settings section"):
        registry.add_profile_settings("demo", DemoSettings)


def test_registry_rejects_duplicate_state_setting_sections() -> None:
    registry = PluginRegistry()
    registry.add_state_settings("demo", DemoState)

    with pytest.raises(ConfigError, match="duplicate state settings section"):
        registry.add_state_settings("demo", DemoState)


def test_registry_stores_diagnostics() -> None:
    registry = PluginRegistry()
    registry.add_diagnostic("demo", lambda: DiagnosticResult(name="demo", status="ok"))

    assert registry.run_diagnostics() == [DiagnosticResult(name="demo", status="ok")]
