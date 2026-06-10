"""Tests for lazy plugin CLI mounting via CliSpec import paths."""

from __future__ import annotations

import sys
import types

import pytest

from untaped import create_app, echo
from untaped.main import build_app
from untaped.plugins import CliSpec, PluginManifest
from untaped.testing import CliInvoker

LAZY_MODULE = "fake_lazy_plugin_cli"


def _lazy_plugin(import_path: str = f"{LAZY_MODULE}:app") -> object:
    class LazyPlugin:
        id = "demo"
        untaped_api_version = 3

        def manifest(self) -> PluginManifest:
            return PluginManifest(clis=(CliSpec(name="demo", import_path=import_path),))

    return LazyPlugin()


@pytest.fixture
def lazy_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType(LAZY_MODULE)
    app = create_app(name="demo", help="Demo lazy plugin command.")

    @app.command(name="ping")
    def ping() -> None:
        echo("pong")

    module.app = app  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, LAZY_MODULE, module)
    return module


def test_lazy_cli_dispatches_after_on_demand_resolution(
    lazy_module: types.ModuleType,
) -> None:
    app = build_app(plugins=[_lazy_plugin()])

    result = CliInvoker().invoke(app, ["demo", "ping"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "pong"


def test_lazy_cli_module_not_imported_for_unrelated_commands() -> None:
    app = build_app(plugins=[_lazy_plugin(import_path="totally_missing_module_xyz:app")])

    result = CliInvoker().invoke(app, ["plugins", "doctor"])

    assert result.exit_code == 0, result.output


def test_lazy_cli_appears_in_root_help(lazy_module: types.ModuleType) -> None:
    app = build_app(plugins=[_lazy_plugin()])

    result = CliInvoker().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "demo" in result.stdout


def test_targeted_lazy_cli_import_failure_fails_cleanly() -> None:
    app = build_app(plugins=[_lazy_plugin(import_path="totally_missing_module_xyz:app")])

    result = CliInvoker().invoke(app, ["demo", "ping"])

    assert result.exit_code == 1
    assert "error:" in result.stderr
    assert "totally_missing_module_xyz" in result.stderr


def test_help_lists_broken_lazy_cli_without_importing_it() -> None:
    app = build_app(plugins=[_lazy_plugin(import_path="totally_missing_module_xyz:app")])

    result = CliInvoker().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "demo" in result.stdout


def test_lazy_cli_subcommand_help_renders_real_app(lazy_module: types.ModuleType) -> None:
    app = build_app(plugins=[_lazy_plugin()])

    result = CliInvoker().invoke(app, ["demo", "--help"])

    assert result.exit_code == 0, result.output
    assert "ping" in result.stdout


def test_root_version_flag_still_works(lazy_module: types.ModuleType) -> None:
    app = build_app(plugins=[_lazy_plugin()])

    result = CliInvoker().invoke(app, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip()


def test_lazy_cli_survives_repeated_invocations(lazy_module: types.ModuleType) -> None:
    app = build_app(plugins=[_lazy_plugin()])
    runner = CliInvoker()

    first = runner.invoke(app, ["demo", "ping"])
    second = runner.invoke(app, ["demo", "ping"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert second.stdout.strip() == "pong"
