"""Tests for the SDK skill install transaction helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

import untaped.skills as skills_module
from untaped.errors import ConfigError
from untaped.skills import SkillInstallScope, SkillInstallTarget
from untaped.tool import SkillAsset


def _skill_dir(tmp_path: Path, name: str = "untaped-demo") -> Path:
    source = tmp_path / "source" / name
    source.mkdir(parents=True)
    source.joinpath("SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Teach agents how to use demo untaped commands.\n"
        "---\n"
        "\n"
        f"# {name}\n",
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


def test_install_skills_installs_one_skill_and_returns_metadata(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    target = tmp_path / "codex-skills"

    results = skills_module.install_skills(
        {source.name: _asset(source)},
        ["untaped-demo"],
        stdin=False,
        all_skills=False,
        target=SkillInstallTarget.codex,
        force=False,
        scope=SkillInstallScope.global_,
        project_dir=None,
        target_dir=target,
    )

    assert [(r.name, r.target, r.scope, r.root, r.destination) for r in results] == [
        (
            "untaped-demo",
            "codex",
            SkillInstallScope.global_,
            target.resolve(),
            target.resolve() / "untaped-demo",
        )
    ]
    assert (target / "untaped-demo" / "SKILL.md").is_file()


def test_install_skills_all_targets_return_deterministic_results(tmp_path: Path) -> None:
    one = _skill_dir(tmp_path, name="untaped-one")
    two = _skill_dir(tmp_path, name="untaped-two")
    project = tmp_path / "project"
    project.mkdir()
    project_root = project.resolve()

    results = skills_module.install_skills(
        {one.name: _asset(one), two.name: _asset(two)},
        [],
        stdin=False,
        all_skills=True,
        target=SkillInstallTarget.all,
        force=False,
        scope=SkillInstallScope.local,
        project_dir=project,
        target_dir=None,
    )

    assert [(r.name, r.target, r.destination) for r in results] == [
        ("untaped-one", "codex", project_root / ".agents" / "skills" / "untaped-one"),
        ("untaped-one", "claude", project_root / ".claude" / "skills" / "untaped-one"),
        ("untaped-two", "codex", project_root / ".agents" / "skills" / "untaped-two"),
        ("untaped-two", "claude", project_root / ".claude" / "skills" / "untaped-two"),
    ]


def test_install_skills_missing_source_aborts_before_any_copy(tmp_path: Path) -> None:
    good = _skill_dir(tmp_path, name="untaped-one")
    missing = tmp_path / "gone" / "untaped-two"
    target = tmp_path / "codex-skills"

    with pytest.raises(ConfigError, match="untaped-two"):
        skills_module.install_skills(
            {
                good.name: _asset(good),
                "untaped-two": SkillAsset(
                    name="untaped-two",
                    source=missing,
                    description="Teach agents how to use two.",
                ),
            },
            [],
            stdin=False,
            all_skills=True,
            target=SkillInstallTarget.codex,
            force=False,
            scope=SkillInstallScope.global_,
            project_dir=None,
            target_dir=target,
        )

    assert not (target / "untaped-one").exists()
    assert not (target / "untaped-two").exists()


def test_install_skills_later_conflict_aborts_before_earlier_install(tmp_path: Path) -> None:
    one = _skill_dir(tmp_path, name="untaped-one")
    two = _skill_dir(tmp_path, name="untaped-two")
    target = tmp_path / "codex-skills"
    target.joinpath("untaped-two").mkdir(parents=True)

    with pytest.raises(ConfigError, match="skill already exists: untaped-two"):
        skills_module.install_skills(
            {one.name: _asset(one), two.name: _asset(two)},
            [],
            stdin=False,
            all_skills=True,
            target=SkillInstallTarget.codex,
            force=False,
            scope=SkillInstallScope.global_,
            project_dir=None,
            target_dir=target,
        )

    assert not (target / "untaped-one").exists()
    assert (target / "untaped-two").is_dir()


@pytest.mark.parametrize(
    ("skill_names", "stdin", "all_skills", "message"),
    [
        (["untaped-demo", "untaped-demo"], False, False, "duplicate skill selected"),
        (["untaped-missing"], False, False, "unknown skill: untaped-missing"),
        (["untaped-demo"], False, True, "provide skill names, --stdin, or --all"),
        ([], True, True, "provide skill names, --stdin, or --all"),
    ],
)
def test_install_skills_selector_errors_preserve_messages(
    tmp_path: Path,
    skill_names: list[str],
    stdin: bool,
    all_skills: bool,
    message: str,
) -> None:
    source = _skill_dir(tmp_path)

    with pytest.raises(ConfigError, match=message):
        skills_module.install_skills(
            {source.name: _asset(source)},
            skill_names,
            stdin=stdin,
            all_skills=all_skills,
            target=SkillInstallTarget.codex,
            force=False,
            scope=SkillInstallScope.global_,
            project_dir=None,
            target_dir=tmp_path / "codex-skills",
        )


def test_install_skills_force_replaces_existing_without_merging(tmp_path: Path) -> None:
    source = _skill_dir(tmp_path)
    target = tmp_path / "codex-skills"
    installed = target / "untaped-demo"
    installed.mkdir(parents=True)
    installed.joinpath("SKILL.md").write_text("old\n")
    installed.joinpath("STALE.md").write_text("stale\n")

    results = skills_module.install_skills(
        {source.name: _asset(source)},
        ["untaped-demo"],
        stdin=False,
        all_skills=False,
        target=SkillInstallTarget.codex,
        force=True,
        scope=SkillInstallScope.global_,
        project_dir=None,
        target_dir=target,
    )

    assert [r.destination for r in results] == [installed]
    assert installed.joinpath("SKILL.md").read_text() == source.joinpath("SKILL.md").read_text()
    assert not installed.joinpath("STALE.md").exists()
    assert not list(target.glob(".untaped-demo.backup-*"))


def test_install_skills_force_restores_existing_when_final_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _skill_dir(tmp_path)
    target = tmp_path / "codex-skills"
    installed = target / "untaped-demo"
    installed.mkdir(parents=True)
    installed.joinpath("SKILL.md").write_text("old\n")
    installed.joinpath("STALE.md").write_text("stale\n")
    real_replace = skills_module.os.replace

    def fail_final_replace(src: Path | str, dst: Path | str) -> None:
        src_path = Path(src)
        dst_path = Path(dst)
        if (
            src_path.name == "untaped-demo"
            and src_path.parent.name.startswith(".untaped-demo.tmp-")
            and dst_path == installed
        ):
            raise RuntimeError("placement failed")
        real_replace(src, dst)

    monkeypatch.setattr(skills_module.os, "replace", fail_final_replace)

    with pytest.raises(RuntimeError, match="placement failed"):
        skills_module.install_skills(
            {source.name: _asset(source)},
            ["untaped-demo"],
            stdin=False,
            all_skills=False,
            target=SkillInstallTarget.codex,
            force=True,
            scope=SkillInstallScope.global_,
            project_dir=None,
            target_dir=target,
        )

    assert installed.joinpath("SKILL.md").read_text() == "old\n"
    assert installed.joinpath("STALE.md").read_text() == "stale\n"
    assert not list(target.glob(".untaped-demo.backup-*"))
