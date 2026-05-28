from __future__ import annotations

import typer
from typer.testing import CliRunner

from untaped.main import build_app
from untaped.plugins import PluginRegistry


class _Plugin:
    id = "demo"

    def register(self, registry: PluginRegistry) -> None:
        app = typer.Typer(no_args_is_help=True)

        @app.command("ping")
        def ping() -> None:
            typer.echo("pong")

        registry.add_cli("demo", app)


class _FailingPlugin:
    id = "broken"

    def register(self, registry: PluginRegistry) -> None:
        raise RuntimeError("boom")


def test_root_app_registers_discovered_plugin_commands() -> None:
    app = build_app(plugins=[_Plugin()])

    result = CliRunner().invoke(app, ["demo", "ping"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "pong"


def test_plugin_load_failures_do_not_break_core_commands() -> None:
    app = build_app(plugins=[_FailingPlugin()])
    runner = CliRunner()

    config_result = runner.invoke(app, ["config", "--help"])
    doctor_result = runner.invoke(app, ["plugins", "doctor"])

    assert config_result.exit_code == 0
    assert doctor_result.exit_code == 1
    assert "broken" in doctor_result.stdout
    assert "boom" in doctor_result.stdout
