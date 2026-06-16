"""Tests for the per-tool ``<tool> skills list/install`` command group factory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.skills_app import build_skills_app
from untaped.testing import CliInvoker
from untaped.tool import SkillAsset, ToolSpec

pytestmark = pytest.mark.usefixtures("_isolated_config")


class _Profile(BaseModel):
    pass


def _skill_dir(tmp_path: Path, name: str = "untaped-demo") -> Path:
    source = tmp_path / "source" / name
    source.mkdir(parents=True)
    source.joinpath("SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Teach agents how to use demo untaped commands.\n"
        "---\n"
        "\n"
        "# Demo\n",
    )
    source.joinpath("references").mkdir()
    source.joinpath("references", "commands.md").write_text("# Commands\n")
    return source


def _asset(source: Path) -> SkillAsset:
    return SkillAsset(
        name=source.name,
        source=source,
        description=f"Teach agents how to use {source.name}.",
    )


def _spec(*assets: SkillAsset) -> ToolSpec:
    return ToolSpec(
        command="untaped-demo",
        section="demo",
        profile_model=_Profile,
        skills=assets,
    )


def test_skills_list_raw_defaults_to_skill_names(tmp_path: Path) -> None:
    app = build_skills_app(_spec(_asset(_skill_dir(tmp_path))))

    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "name"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["untaped-demo"]


def test_skills_install_copies_skill_directory_and_marker(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    app = build_skills_app(_spec(_asset(source)))
    project = tmp_path / "project"
    project.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "install",
            "untaped-demo",
            "--target",
            "claude",
            "--scope",
            "local",
            "--project-dir",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    installed = project / ".claude" / "skills" / "untaped-demo"
    assert installed.joinpath("SKILL.md").read_text() == source.joinpath("SKILL.md").read_text()
    assert installed.joinpath("references", "commands.md").read_text() == "# Commands\n"
    marker = json.loads(installed.joinpath(".untaped-skill.json").read_text())
    assert marker == {
        "install_root": str(project / ".claude" / "skills"),
        "name": "untaped-demo",
        "scope": "local",
        "source": str(source),
        "target": "claude",
    }
    assert "installed skill: untaped-demo" in result.output


def test_skills_install_all_installs_every_skill(tmp_path: Path) -> None:
    first = _skill_dir(tmp_path, name="untaped-one")
    second = _skill_dir(tmp_path, name="untaped-two")
    app = build_skills_app(_spec(_asset(first), _asset(second)))
    target = tmp_path / "codex-skills"

    result = CliInvoker().invoke(
        app,
        ["install", "--all", "--target", "codex", "--target-dir", str(target)],
    )

    assert result.exit_code == 0, result.output
    assert target.joinpath("untaped-one", "SKILL.md").is_file()
    assert target.joinpath("untaped-two", "SKILL.md").is_file()


def test_skills_install_unknown_name_is_rejected(tmp_path: Path) -> None:
    app = build_skills_app(_spec(_asset(_skill_dir(tmp_path))))
    target = tmp_path / "codex-skills"

    result = CliInvoker().invoke(
        app,
        ["install", "untaped-missing", "--target", "codex", "--target-dir", str(target)],
    )

    assert result.exit_code != 0
    assert "untaped-missing" in result.stderr
    assert not target.exists()
