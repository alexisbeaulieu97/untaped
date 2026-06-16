"""Tests for the SDK tool composition contract (ToolSpec / SkillAsset)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.tool import SkillAsset, ToolSpec


class _Settings(BaseModel):
    token: str | None = None


class _State(BaseModel):
    cache: dict[str, str] = {}


def test_toolspec_holds_command_section_and_model() -> None:
    spec = ToolSpec(command="untaped-github", section="github", profile_model=_Settings)
    assert spec.command == "untaped-github"
    assert spec.section == "github"
    assert spec.profile_model is _Settings
    assert spec.state_model is None
    assert spec.skills == ()


def test_toolspec_accepts_state_model_and_normalizes_skills_to_tuple() -> None:
    skill = SkillAsset(name="untaped-workspace", source=Path("/x"), description="d")
    spec = ToolSpec(
        command="untaped-workspace",
        section="workspace",
        profile_model=_Settings,
        state_model=_State,
        skills=[skill],
    )
    assert spec.state_model is _State
    assert spec.skills == (skill,)


def test_toolspec_rejects_empty_command() -> None:
    with pytest.raises(ConfigError):
        ToolSpec(command="", section="github", profile_model=_Settings)


def test_toolspec_rejects_empty_section() -> None:
    with pytest.raises(ConfigError):
        ToolSpec(command="untaped-github", section="", profile_model=_Settings)


def test_toolspec_rejects_non_basemodel_profile_model() -> None:
    with pytest.raises(ConfigError):
        ToolSpec(command="untaped-github", section="github", profile_model=dict)


def test_toolspec_rejects_non_basemodel_state_model() -> None:
    with pytest.raises(ConfigError):
        ToolSpec(
            command="untaped-github",
            section="github",
            profile_model=_Settings,
            state_model=dict,
        )


def test_toolspec_rejects_duplicate_skill_names() -> None:
    a = SkillAsset(name="dup", source=Path("/a"), description="d")
    b = SkillAsset(name="dup", source=Path("/b"), description="d")
    with pytest.raises(ConfigError):
        ToolSpec(
            command="untaped-github",
            section="github",
            profile_model=_Settings,
            skills=[a, b],
        )


def test_skillasset_holds_fields() -> None:
    asset = SkillAsset(name="untaped-github", source=Path("/a/b"), description="Use it.")
    assert asset.name == "untaped-github"
    assert asset.source == Path("/a/b")
    assert asset.description == "Use it."


def test_skillasset_rejects_empty_name() -> None:
    with pytest.raises(ConfigError):
        SkillAsset(name="", source=Path("/a"), description="d")
