"""End-to-end tests for the SDK profile command-group factory.

``build_profile_app`` produces the ``<tool> profile …`` command group that
``run_tool`` mounts on every tool, absorbed from the retired untaped-profile
plugin. Behaviour is exercised through the CLI (the public surface), not the
internal use-case classes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped.config_file import read_config_dict
from untaped.profile import build_profile_app
from untaped.settings import get_settings, register_profile_settings
from untaped.testing import CliInvoker


class _GithubSettings(BaseModel):
    token: SecretStr | None = None


@pytest.fixture
def app(_isolated_config: Path):
    register_profile_settings("github", _GithubSettings)
    get_settings.cache_clear()
    return build_profile_app(command="untaped-github")


def _seed(cfg: Path) -> None:
    cfg.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    github:\n      token: ghp_secret\n"
        "  stage:\n    log_level: DEBUG\n"
        "active: prod\n",
        encoding="utf-8",
    )


def test_list_outputs_all_profiles(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.splitlines()) == ["default", "prod", "stage"]


def test_list_empty_hint_names_tool_command(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "untaped-github profile create" in result.stderr


def test_current_reports_active_profile(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["current"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "prod"


def test_create_then_use_persists_active(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    assert CliInvoker().invoke(app, ["create", "dev"]).exit_code == 0
    assert CliInvoker().invoke(app, ["use", "dev"]).exit_code == 0
    data = read_config_dict(_isolated_config)
    assert "dev" in data["profiles"]
    assert data["active"] == "dev"


def test_delete_active_profile_is_refused(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["delete", "prod", "--yes"])
    assert result.exit_code != 0
    assert "active" in result.stderr.lower()
    assert "prod" in read_config_dict(_isolated_config)["profiles"]


def test_delete_with_yes_removes_profile(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["delete", "stage", "--yes"])
    assert result.exit_code == 0, result.output
    assert "stage" not in read_config_dict(_isolated_config)["profiles"]


def test_delete_accepts_dash_y_alias(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["delete", "stage", "-y"])
    assert result.exit_code == 0, result.output
    assert "deleted profile: stage" in result.stderr


def test_delete_tty_authority_is_the_context_stdin(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real-TTY process stdin must not bypass refusal when invocation stdin is a pipe."""
    from untaped.testing import TtyStringIO, invoke_cli

    _seed(_isolated_config)
    monkeypatch.setattr("sys.stdin", TtyStringIO())
    result = invoke_cli(app, ["delete", "stage"])
    assert result.exit_code == 1
    assert "requires --yes" in f"{result.stderr}{result.exception or ''}"


def test_delete_interactive_decline_leaves_profile(app, _isolated_config: Path) -> None:
    from untaped.testing import ScriptedPromptBackend, invoke_cli

    _seed(_isolated_config)
    backend = ScriptedPromptBackend(confirms=[False])
    result = invoke_cli(app, ["delete", "stage"], interactive=True, prompt_backend=backend)
    assert result.exit_code == 1
    assert backend.calls[0][0] == "confirm"
    assert "delete cancelled" in result.stderr
    assert "stage" in _isolated_config.read_text(encoding="utf-8")


def test_rename_updates_profiles(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["rename", "stage", "staging"])
    assert result.exit_code == 0, result.output
    profiles = read_config_dict(_isolated_config)["profiles"]
    assert "staging" in profiles
    assert "stage" not in profiles


def test_show_redacts_secrets(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["show", "prod"])
    assert result.exit_code == 0, result.output
    assert "ghp_secret" not in result.stdout
    assert "***" in result.stdout
