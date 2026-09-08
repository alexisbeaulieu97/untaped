"""Tests for the root ``untaped profile …`` command group.

The profile surface is capability-agnostic (it operates on the shared
``profiles`` layout and names the unified executable in its guidance.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped.config_file import read_config_dict
from untaped.management.profile import build_root_profile_app
from untaped.settings import get_settings, register_profile_settings
from untaped.testing import CliInvoker, TtyStringIO, invoke_cli

pytestmark = pytest.mark.usefixtures("_isolated_config")

_MANAGEMENT_VERBS = ("list", "show", "use", "current", "create", "delete", "rename")


class _GithubSettings(BaseModel):
    token: SecretStr | None = None


@pytest.fixture
def app(_isolated_config: Path):
    register_profile_settings("github", _GithubSettings)
    get_settings.cache_clear()
    return build_root_profile_app(command="untaped")


def _seed(cfg: Path) -> None:
    cfg.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    github:\n      token: ghp_secret\n"
        "  stage:\n    log_level: DEBUG\n"
        "active: prod\n",
        encoding="utf-8",
    )


def test_root_profile_exposes_all_verbs() -> None:
    app = build_root_profile_app(command="untaped")
    for verb in _MANAGEMENT_VERBS:
        assert verb in app


def test_empty_hint_names_unified_executable() -> None:
    app = build_root_profile_app(command="untaped")
    result = CliInvoker().invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "`untaped profile create <name>`" in result.output


def test_list_marks_active_profile(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default: {}\n  work: {}\nactive: work\n", encoding="utf-8"
    )
    app = build_root_profile_app(command="untaped")
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "name"])
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.splitlines()) == ["default", "work"]


def test_create_use_and_current_round_trip(_isolated_config: Path) -> None:
    app = build_root_profile_app(command="untaped")
    assert CliInvoker().invoke(app, ["create", "work"]).exit_code == 0
    use_result = CliInvoker().invoke(app, ["use", "work"])
    assert use_result.exit_code == 0, use_result.output
    assert "active profile: work" in use_result.output
    current = CliInvoker().invoke(app, ["current"])
    assert current.exit_code == 0, current.output
    assert current.stdout.strip() == "work"


def test_delete_active_profile_is_refused(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["delete", "prod", "--yes"])
    assert result.exit_code != 0
    assert "active" in result.stderr.lower()
    assert "prod" in read_config_dict(_isolated_config)["profiles"]


def test_delete_accepts_dash_y_alias(app, _isolated_config: Path) -> None:
    _seed(_isolated_config)
    result = CliInvoker().invoke(app, ["delete", "stage", "-y"])
    assert result.exit_code == 0, result.output
    assert "deleted profile: stage" in result.stderr


def test_delete_without_confirmation_does_not_mutate_noninteractive_input(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(_isolated_config)
    monkeypatch.setattr("sys.stdin", TtyStringIO())
    result = invoke_cli(app, ["delete", "stage"])
    assert result.exit_code == 1
    assert "requires --yes" in f"{result.stderr}{result.exception or ''}"
    assert "stage" in read_config_dict(_isolated_config)["profiles"]


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
