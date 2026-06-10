"""Skill CLI tests for listing and installing agent skills."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from untaped.plugins import PluginRegistry, SkillSpec, set_current_registry
from untaped.skills import app as skills_app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


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


def _register_skill(source: Path, name: str = "untaped-demo") -> None:
    registry = PluginRegistry()
    registry.add_skill(
        SkillSpec(
            name=name,
            source=source,
            description="Teach agents how to use demo untaped commands.",
        )
    )
    set_current_registry(registry)


def test_skills_list_json_reports_registered_skills(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    _register_skill(source)

    result = CliInvoker().invoke(skills_app, ["list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "name": "untaped-demo",
            "description": "Teach agents how to use demo untaped commands.",
            "source": str(source),
        }
    ]


def test_skills_list_raw_defaults_to_skill_names(tmp_path: Path) -> None:
    _register_skill(_skill_dir(tmp_path))

    result = CliInvoker().invoke(skills_app, ["list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["untaped-demo"]


def test_skills_install_no_args_shows_help(tmp_path: Path) -> None:
    _register_skill(_skill_dir(tmp_path))

    result = CliInvoker().invoke(skills_app, ["install"])

    assert result.exit_code == 0, result.output
    assert "Usage: skills install" in result.output


def test_skills_install_rejects_multiple_selector_sources(tmp_path: Path) -> None:
    _register_skill(_skill_dir(tmp_path))

    result = CliInvoker().invoke(
        skills_app,
        ["install", "untaped-demo", "--stdin"],
        input="untaped-other\n",
    )

    assert result.exit_code == 1
    assert "provide skill names, --stdin, or --all; not more than one" in result.output


def test_skills_install_rejects_duplicate_selected_names_before_writing(tmp_path: Path) -> None:
    _register_skill(_skill_dir(tmp_path))
    target = tmp_path / "codex-skills"

    result = CliInvoker().invoke(
        skills_app,
        [
            "install",
            "untaped-demo",
            "untaped-demo",
            "--target",
            "codex",
            "--target-dir",
            str(target),
        ],
    )

    assert result.exit_code == 1
    assert "duplicate skill selected: untaped-demo" in result.output
    assert not target.exists()


def test_skills_install_copies_skill_directory_and_marker(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    _register_skill(source)
    target = tmp_path / "codex-skills"

    result = CliInvoker().invoke(
        skills_app,
        ["install", "untaped-demo", "--target", "codex", "--target-dir", str(target)],
    )

    assert result.exit_code == 0, result.output
    installed = target / "untaped-demo"
    assert installed.joinpath("SKILL.md").read_text() == source.joinpath("SKILL.md").read_text()
    assert installed.joinpath("references", "commands.md").read_text() == "# Commands\n"
    assert json.loads(installed.joinpath(".untaped-skill.json").read_text()) == {
        "install_root": str(target),
        "name": "untaped-demo",
        "scope": "global",
        "source": str(source),
        "target": "codex",
    }
    assert "installed skill: untaped-demo" in result.output


def test_skills_install_uses_user_agents_dir_for_codex_global_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_skill(_skill_dir(tmp_path))
    home = tmp_path / "home"
    legacy_codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(legacy_codex_home))

    result = CliInvoker().invoke(skills_app, ["install", "untaped-demo", "--target", "codex"])

    assert result.exit_code == 0, result.output
    assert home.joinpath(".agents", "skills", "untaped-demo", "SKILL.md").is_file()
    assert not legacy_codex_home.exists()


def test_skills_install_uses_claude_skills_dir_for_global_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_skill(_skill_dir(tmp_path))
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    result = CliInvoker().invoke(skills_app, ["install", "untaped-demo", "--target", "claude"])

    assert result.exit_code == 0, result.output
    assert home.joinpath(".claude", "skills", "untaped-demo", "SKILL.md").is_file()


def test_skills_install_local_scope_installs_to_agent_project_dirs(tmp_path: Path) -> None:
    _register_skill(_skill_dir(tmp_path))
    project = tmp_path / "project"
    project.mkdir()

    result = CliInvoker().invoke(
        skills_app,
        [
            "install",
            "untaped-demo",
            "--target",
            "all",
            "--scope",
            "local",
            "--project-dir",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    assert project.joinpath(".agents", "skills", "untaped-demo", "SKILL.md").is_file()
    assert project.joinpath(".claude", "skills", "untaped-demo", "SKILL.md").is_file()


def test_skills_install_local_scope_defaults_to_git_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_skill(_skill_dir(tmp_path))
    repo = tmp_path / "repo"
    nested = repo / "packages" / "api"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    monkeypatch.chdir(nested)

    result = CliInvoker().invoke(
        skills_app,
        ["install", "untaped-demo", "--target", "codex", "--scope", "local"],
    )

    assert result.exit_code == 0, result.output
    assert repo.joinpath(".agents", "skills", "untaped-demo", "SKILL.md").is_file()
    assert not nested.joinpath(".agents").exists()


def test_skills_install_local_scope_defaults_to_cwd_without_git_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_skill(_skill_dir(tmp_path))
    project = tmp_path / "plain-project"
    project.mkdir()
    monkeypatch.chdir(project)

    result = CliInvoker().invoke(
        skills_app,
        ["install", "untaped-demo", "--target", "claude", "--scope", "local"],
    )

    assert result.exit_code == 0, result.output
    assert project.joinpath(".claude", "skills", "untaped-demo", "SKILL.md").is_file()


def test_skills_install_marker_records_local_scope_and_install_root(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    _register_skill(source)
    project = tmp_path / "project"
    project.mkdir()

    result = CliInvoker().invoke(
        skills_app,
        [
            "install",
            "untaped-demo",
            "--target",
            "codex",
            "--scope",
            "local",
            "--project-dir",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    install_root = project / ".agents" / "skills"
    marker = json.loads(install_root.joinpath("untaped-demo", ".untaped-skill.json").read_text())
    assert marker == {
        "install_root": str(install_root),
        "name": "untaped-demo",
        "scope": "local",
        "source": str(source),
        "target": "codex",
    }


def test_skills_install_project_dir_requires_local_scope(tmp_path: Path) -> None:
    _register_skill(_skill_dir(tmp_path))
    project = tmp_path / "project"
    project.mkdir()

    result = CliInvoker().invoke(
        skills_app,
        ["install", "untaped-demo", "--target", "codex", "--project-dir", str(project)],
    )

    assert result.exit_code == 1
    assert "--project-dir requires --scope local" in result.output


def test_skills_install_target_dir_requires_single_target(tmp_path: Path) -> None:
    _register_skill(_skill_dir(tmp_path))

    result = CliInvoker().invoke(
        skills_app,
        [
            "install",
            "untaped-demo",
            "--target",
            "all",
            "--target-dir",
            str(tmp_path / "skills"),
        ],
    )

    assert result.exit_code == 1
    assert "--target-dir requires --target codex or --target claude" in result.output


def test_skills_install_project_dir_cannot_combine_with_target_dir(tmp_path: Path) -> None:
    _register_skill(_skill_dir(tmp_path))
    project = tmp_path / "project"
    project.mkdir()

    result = CliInvoker().invoke(
        skills_app,
        [
            "install",
            "untaped-demo",
            "--target",
            "codex",
            "--scope",
            "local",
            "--project-dir",
            str(project),
            "--target-dir",
            str(tmp_path / "skills"),
        ],
    )

    assert result.exit_code == 1
    assert "--project-dir cannot be combined with --target-dir" in result.output


def test_skills_install_fails_when_target_exists_without_force(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    _register_skill(source)
    target = tmp_path / "codex-skills"
    target.joinpath("untaped-demo").mkdir(parents=True)
    target.joinpath("untaped-demo", "SKILL.md").write_text("local edit\n")

    result = CliInvoker().invoke(
        skills_app,
        ["install", "untaped-demo", "--target", "codex", "--target-dir", str(target)],
    )

    assert result.exit_code == 1
    assert "skill already exists: untaped-demo" in result.output
    assert target.joinpath("untaped-demo", "SKILL.md").read_text() == "local edit\n"


def test_skills_install_force_replaces_existing_target(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    _register_skill(source)
    target = tmp_path / "codex-skills"
    target.joinpath("untaped-demo").mkdir(parents=True)
    target.joinpath("untaped-demo", "SKILL.md").write_text("local edit\n")

    result = CliInvoker().invoke(
        skills_app,
        [
            "install",
            "untaped-demo",
            "--target",
            "codex",
            "--target-dir",
            str(target),
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (
        target.joinpath("untaped-demo", "SKILL.md").read_text()
        == source.joinpath("SKILL.md").read_text()
    )


def test_skills_install_all_installs_every_registered_skill(tmp_path: Path) -> None:
    first = _skill_dir(tmp_path, name="untaped-one")
    second = _skill_dir(tmp_path, name="untaped-two")
    registry = PluginRegistry()
    for source in (first, second):
        registry.add_skill(
            SkillSpec(
                name=source.name,
                source=source,
                description=f"Teach agents how to use {source.name}.",
            )
        )
    set_current_registry(registry)
    target = tmp_path / "codex-skills"

    result = CliInvoker().invoke(
        skills_app,
        ["install", "--all", "--target", "codex", "--target-dir", str(target)],
    )

    assert result.exit_code == 0, result.output
    assert target.joinpath("untaped-one", "SKILL.md").is_file()
    assert target.joinpath("untaped-two", "SKILL.md").is_file()
