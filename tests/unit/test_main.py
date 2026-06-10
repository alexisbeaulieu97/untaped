import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import pytest
import yaml
from cyclopts import Parameter

from untaped import ProfileOverrideOption, create_app, echo, get_settings, profile_override
from untaped.main import build_app
from untaped.plugins import PluginRegistry
from untaped.testing import CliInvoker


class _ProfileEnvProbePlugin:
    id = "profile-env-probe"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        probe_app = create_app(name="probe")

        @probe_app.command(name="current")
        def current() -> None:
            echo(os.environ.get("UNTAPED_PROFILE", ""))

        registry.add_cli("probe", probe_app)


class _ProfileSettingsProbePlugin:
    id = "profile-settings-probe"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        probe_app = create_app(name="probe")

        @probe_app.command(name="log-level")
        def log_level(profile: ProfileOverrideOption = None) -> None:
            with profile_override(profile):
                echo(get_settings().log_level)

        registry.add_cli("probe", probe_app)


class _PassthroughProbePlugin:
    id = "passthrough-probe"
    untaped_api_version = 2

    def register(self, registry: PluginRegistry) -> None:
        probe_app = create_app(name="probe")

        @probe_app.command(name="run")
        def run(
            *args: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
        ) -> None:
            echo(
                json.dumps(
                    {
                        "args": list(args),
                        "profile": os.environ.get("UNTAPED_PROFILE"),
                    },
                    sort_keys=True,
                )
            )

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
    result = CliInvoker().invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "config" in output
    assert "awx" not in output
    assert "workspace" not in output
    assert "github" not in output
    assert "Manage configuration profiles" not in output


def test_help_describes_root_profile_without_rich_markup(app: object) -> None:
    result = CliInvoker().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "UNTAPED_PROFILE environment variable" in result.stdout
    assert "must precede the command" in result.stdout
    assert "UNTAPED_PROFILE=)" not in result.stdout
    assert result.stdout.count("--profile") == 1
    assert "Global options:" not in result.stdout


def test_subcommand_help_does_not_repeat_root_profile_help(app: object) -> None:
    result = CliInvoker().invoke(app, ["config", "--help"])

    assert result.exit_code == 0
    assert result.stdout.count("--profile") == 1
    assert "Global options:" not in result.stdout


def test_help_lists_skills_core_command(app: object) -> None:
    result = CliInvoker().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "skills" in result.stdout


def test_install_completion_help_is_available(app: object) -> None:
    result = CliInvoker().invoke(app, ["--install-completion", "--help"])

    assert result.exit_code == 0, result.output
    assert "Install shell completion" in result.stdout


def test_skills_list_includes_builtin_untaped_skill(app: object) -> None:
    result = CliInvoker().invoke(app, ["skills", "list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert "untaped" in result.stdout.splitlines()


def test_config_subcommand_help(app: object) -> None:
    result = CliInvoker().invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "set" in result.stdout
    assert "unset" in result.stdout


def test_root_parse_errors_exit_2_and_stderr(app: object) -> None:
    result = CliInvoker().invoke(app, ["--bad"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "error: Unknown command" in result.stderr


def test_root_profile_flag_is_visible_to_plugin_commands() -> None:
    """Root ``--profile`` remains core plumbing even when profile is external."""
    app = build_app(plugins=[_ProfileEnvProbePlugin()])

    result = CliInvoker().invoke(app, ["--profile", "stage", "probe", "current"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["stage"]


def test_trailing_profile_flag_is_not_stolen_from_passthrough_command() -> None:
    app = build_app(plugins=[_PassthroughProbePlugin()])

    result = CliInvoker().invoke(app, ["probe", "run", "git", "log", "--profile", "stage"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "args": ["git", "log", "--profile", "stage"],
        "profile": None,
    }


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
    runner = CliInvoker()
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

    result = CliInvoker().invoke(app, ["probe", "log-level", "--profile", "stage"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["DEBUG"]
    assert yaml.safe_load(_isolate_config.read_text())["active"] == "prod"
