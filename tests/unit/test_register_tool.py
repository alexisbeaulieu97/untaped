"""Tests for per-tool settings registration + app_context resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.tool import ToolSpec, app_context, register_tool


class GithubSettings(BaseModel):
    token: str | None = None
    base_url: str = "https://api.github.com"


class WorkspaceSettings(BaseModel):
    root: str = "~"


class WorkspaceState(BaseModel):
    workspaces: list[dict[str, str]] = []


def test_register_tool_resolves_own_section(_isolated_config: Path) -> None:
    _isolated_config.write_text("github:\n  token: t\n", encoding="utf-8")
    register_tool(
        ToolSpec(command="untaped-github", section="github", profile_model=GithubSettings)
    )
    assert app_context().section("github", GithubSettings).token == "t"


def test_register_tool_ignores_foreign_sections(_isolated_config: Path) -> None:
    _isolated_config.write_text("github:\n  token: t\njira:\n  url: x\n", encoding="utf-8")
    register_tool(
        ToolSpec(command="untaped-github", section="github", profile_model=GithubSettings)
    )
    # Resolves cleanly despite the unregistered `jira` section in the shared file.
    assert app_context().section("github", GithubSettings).token == "t"


def test_register_tool_registers_state_model(_isolated_config: Path) -> None:
    _isolated_config.write_text(
        "workspace:\n  workspaces:\n  - {name: a, path: /a}\n", encoding="utf-8"
    )
    register_tool(
        ToolSpec(
            command="untaped-workspace",
            section="workspace",
            profile_model=WorkspaceSettings,
            state_model=WorkspaceState,
        )
    )
    state = app_context().section("workspace", WorkspaceState)
    assert state.workspaces == [{"name": "a", "path": "/a"}]


def test_register_tool_rejects_overlapping_profile_state_fields(_isolated_config: Path) -> None:
    class Profile(BaseModel):
        shared: str = ""

    class State(BaseModel):
        shared: str = ""

    with pytest.raises(ConfigError):
        register_tool(ToolSpec(command="x", section="x", profile_model=Profile, state_model=State))


def test_toolspec_and_skillasset_exported_from_api() -> None:
    from untaped.api import SkillAsset, ToolSpec, app_context, register_tool  # noqa: F401
