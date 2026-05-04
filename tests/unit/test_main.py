import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner
from untaped_core import get_settings

from untaped.main import app


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


def test_help_lists_all_domains() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "config" in output
    assert "workspace" in output
    assert "awx" in output
    assert "github" in output
    assert "profile" in output


def test_workspace_subcommand_help() -> None:
    result = CliRunner().invoke(app, ["workspace", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout


def test_config_subcommand_help() -> None:
    result = CliRunner().invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "set" in result.stdout
    assert "unset" in result.stdout


def test_profile_subcommand_help() -> None:
    result = CliRunner().invoke(app, ["profile", "--help"])
    assert result.exit_code == 0
    for cmd in ("list", "show", "use", "current", "create", "delete", "rename"):
        assert cmd in result.stdout


def test_root_profile_flag_reflected_by_profile_current(_isolate_config: Path) -> None:
    """`untaped --profile stage profile current` must report 'stage' with
    source=env (the root flag stuffs UNTAPED_PROFILE into os.environ)."""
    _isolate_config.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    log_level: WARNING\n"
        "  stage:\n    log_level: DEBUG\n"
        "active: prod\n"
    )
    result = CliRunner().invoke(app, ["--profile", "stage", "profile", "current"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["stage"]
    assert "(source: env)" in result.stderr


def test_root_profile_flag_overrides_active(_isolate_config: Path) -> None:
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
