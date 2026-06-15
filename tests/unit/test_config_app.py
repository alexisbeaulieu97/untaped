"""End-to-end tests for the SDK config command-group factory.

``build_config_app`` produces the ``<tool> config …`` group that ``run_tool``
mounts. Key model: bare keys address the tool's own section; ``http.*`` and
``ui.theme`` are SDK globals written at the top level; tool-managed state
fields are not settable. Exercised through the CLI (the public surface).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped import get_settings
from untaped.config_app import build_config_app
from untaped.config_file import read_config_dict
from untaped.settings import register_settings_layout
from untaped.settings_layout import ProfilesSettingsLayout
from untaped.testing import CliInvoker
from untaped.tool import ToolSpec, register_tool


class _GithubSettings(BaseModel):
    token: SecretStr | None = None
    base_url: str = "https://api.github.com"


GH_SPEC = ToolSpec(command="untaped-github", section="github", profile_model=_GithubSettings)


@pytest.fixture
def app(_isolated_config: Path):
    register_settings_layout(lambda: ProfilesSettingsLayout(), key="sdk:profiles")
    register_tool(GH_SPEC)
    get_settings.cache_clear()
    return build_config_app(GH_SPEC)


def test_set_bare_key_writes_to_section_within_a_profile(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "token", "ghp_x"])
    assert result.exit_code == 0, result.output
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["github"]["token"] == "ghp_x"


def test_set_http_writes_top_level_global(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "false"])
    assert result.exit_code == 0, result.output
    data = read_config_dict(_isolated_config)
    assert data["http"] == {"verify_ssl": False}
    assert "profiles" not in data  # global, not profile-scoped


def test_set_ui_theme_writes_top_level_global(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "ui.theme", "quiet"])
    assert result.exit_code == 0, result.output
    assert read_config_dict(_isolated_config)["ui"] == {"theme": "quiet"}


def test_get_bare_key_reads_effective_value(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      base_url: https://ghe.example\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["get", "base_url"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "https://ghe.example"


def test_list_shows_section_keys(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "key"])
    assert result.exit_code == 0, result.output
    keys = result.stdout.splitlines()
    assert "github.token" in keys
    assert "github.base_url" in keys


def test_get_redacts_secret_value(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      token: s3cr3t\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["get", "token"])
    assert result.exit_code == 0, result.output
    assert "s3cr3t" not in result.stdout


def test_unknown_key_is_rejected(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "bogus", "x"])
    assert result.exit_code != 0
    assert "bogus" in result.stderr


def test_unset_bare_key_removes_from_section(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      token: s\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["unset", "token"])
    assert result.exit_code == 0, result.output
    github = read_config_dict(_isolated_config)["profiles"]["default"].get("github", {})
    assert "token" not in github


def test_state_field_is_not_settable(_isolated_config: Path) -> None:
    class _WsProfile(BaseModel):
        root: str = "~"

    class _WsState(BaseModel):
        workspaces: list[str] = []

    spec = ToolSpec(
        command="untaped-workspace",
        section="workspace",
        profile_model=_WsProfile,
        state_model=_WsState,
    )
    register_settings_layout(lambda: ProfilesSettingsLayout(), key="sdk:profiles")
    register_tool(spec)
    get_settings.cache_clear()
    app = build_config_app(spec)

    result = CliInvoker().invoke(app, ["set", "workspaces", "[]"])
    assert result.exit_code != 0
    assert "workspaces" in result.stderr
