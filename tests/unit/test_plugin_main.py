from __future__ import annotations

from pathlib import Path

import pytest

from untaped import create_app, echo
from untaped.main import build_app
from untaped.plugins import PluginRegistry
from untaped.settings import get_settings, reset_config_registry_for_tests
from untaped.testing import CliInvoker


class _Plugin:
    id = "demo"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        app = create_app(name="demo")

        @app.command(name="ping")
        def ping() -> None:
            echo("pong")

        registry.add_cli("demo", app)


class _FailingPlugin:
    id = "broken"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        raise RuntimeError("boom")


class _CoreCommandShadowPlugin:
    id = "shadow"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        app = create_app(name="config", help="Fake config command.")
        registry.add_cli("config", app)


def test_root_app_registers_discovered_plugin_commands() -> None:
    app = build_app(plugins=[_Plugin()])

    result = CliInvoker().invoke(app, ["demo", "ping"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "pong"


def test_plugin_load_failures_do_not_break_core_commands() -> None:
    app = build_app(plugins=[_FailingPlugin()])
    runner = CliInvoker()

    config_result = runner.invoke(app, ["config", "--help"])
    doctor_result = runner.invoke(app, ["plugins", "doctor"])

    assert config_result.exit_code == 0
    assert doctor_result.exit_code == 1
    assert "broken" in doctor_result.stdout
    assert "boom" in doctor_result.stdout


def test_plugin_cannot_shadow_builtin_core_commands() -> None:
    app = build_app(plugins=[_CoreCommandShadowPlugin()])
    runner = CliInvoker()

    config_result = runner.invoke(app, ["config", "--help"])
    doctor_result = runner.invoke(app, ["plugins", "doctor"])

    assert config_result.exit_code == 0
    assert "Inspect and modify" in config_result.stdout
    assert "Fake config command" not in config_result.stdout
    assert doctor_result.exit_code == 1
    assert "shadow" in doctor_result.stdout
    assert "reserved CLI command" in doctor_result.stdout


def test_config_command_works_without_plugin_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    reset_config_registry_for_tests()
    get_settings.cache_clear()

    app = build_app(plugins=[])
    result = CliInvoker().invoke(app, ["config", "list"])

    assert result.exit_code == 0, result.output
    assert "log_level" in result.stdout
    assert "awx." not in result.stdout
    assert "workspace." not in result.stdout
