import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from untaped import ProfileOverrideOption, get_settings, profile_override
from untaped.main import build_app
from untaped.plugins import PluginRegistry


class _ProfileEnvProbePlugin:
    id = "profile-env-probe"

    def register(self, registry: PluginRegistry) -> None:
        probe_app = typer.Typer(no_args_is_help=True)

        @probe_app.command("current")
        def current() -> None:
            typer.echo(os.environ.get("UNTAPED_PROFILE", ""))

        registry.add_cli("probe", probe_app)


class _ProfileSettingsProbePlugin:
    id = "profile-settings-probe"

    def register(self, registry: PluginRegistry) -> None:
        probe_app = typer.Typer(no_args_is_help=True)

        @probe_app.command("log-level")
        def log_level(profile: ProfileOverrideOption = None) -> None:
            with profile_override(profile):
                typer.echo(get_settings().log_level)

        registry.add_cli("probe", probe_app)


@pytest.fixture
def app() -> object:
    return build_app(plugins=[])


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    get_settings.cache_clear()
    yield cfg
    # `--profile` writes to os.environ directly, so monkeypatch can't roll it back.
    os.environ.pop("UNTAPED_PROFILE", None)
    get_settings.cache_clear()


def test_help_lists_core_commands_only_without_plugins(app: object) -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "config" in output
    assert "awx" not in output
    assert "workspace" not in output
    assert "github" not in output
    assert "Manage configuration profiles" not in output


def test_help_lists_skills_core_command(app: object) -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "skills" in result.stdout


def test_skills_list_includes_builtin_untaped_skill(app: object) -> None:
    result = CliRunner().invoke(app, ["skills", "list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert "untaped" in result.stdout.splitlines()


def test_config_subcommand_help(app: object) -> None:
    result = CliRunner().invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "set" in result.stdout
    assert "unset" in result.stdout


def test_root_profile_flag_is_visible_to_plugin_commands() -> None:
    """Root ``--profile`` remains core plumbing even when profile is external."""
    app = build_app(plugins=[_ProfileEnvProbePlugin()])

    result = CliRunner().invoke(app, ["--profile", "stage", "probe", "current"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["stage"]


def test_root_profile_flag_overrides_active(app: object, _isolate_config: Path) -> None:
    """``untaped --profile <name> ...`` swaps the active profile for the
    invocation without touching the persisted ``active:``."""
    _isolate_config.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    log_level: WARNING\n"
        "  stage:\n    log_level: DEBUG\n"
        "active: prod\n"
    )
    runner = CliRunner()
    # Without --profile, log_level reads from prod (WARNING)
    result = runner.invoke(
        app,
        ["config", "list", "--format", "raw", "--columns", "key", "--columns", "value"],
    )
    assert "log_level\tWARNING" in result.stdout
    # With --profile stage, log_level reads from stage (DEBUG)
    result = runner.invoke(
        app,
        [
            "--profile",
            "stage",
            "config",
            "list",
            "--format",
            "raw",
            "--columns",
            "key",
            "--columns",
            "value",
        ],
    )
    assert "log_level\tDEBUG" in result.stdout
    # Persisted active is unchanged
    assert yaml.safe_load(_isolate_config.read_text())["active"] == "prod"


def test_command_local_profile_flag_overrides_plugin_read_command(
    _isolate_config: Path,
) -> None:
    """Plugin commands can opt into order-flexible profile selection."""
    _isolate_config.write_text(
        "profiles:\n  prod:\n    log_level: WARNING\n  stage:\n    log_level: DEBUG\nactive: prod\n"
    )
    app = build_app(plugins=[_ProfileSettingsProbePlugin()])

    result = CliRunner().invoke(app, ["probe", "log-level", "--profile", "stage"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["DEBUG"]
    assert yaml.safe_load(_isolate_config.read_text())["active"] == "prod"
