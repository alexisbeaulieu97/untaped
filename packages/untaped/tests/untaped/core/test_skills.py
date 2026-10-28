"""Tests for the SDK skill install transaction helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

import untaped.core.skills as skills_module
from untaped.core.skills import PackagedSkill, SkillInstallScope, SkillInstallTarget
from untaped.sdk import SkillAsset, checkout_root
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.git import run_git


def _skill(name: str = "untaped-demo") -> PackagedSkill:
    asset = SkillAsset(name=name, description=f"Teach agents how to use {name}.")
    return PackagedSkill(asset=asset, plugin="demo", distribution="untaped-demo")


def test_skill_install_scope_vocabulary_is_not_profile_vocabulary() -> None:
    assert SkillInstallScope.global_.value == "global"
    assert SkillInstallScope.local.value == "local"


def test_install_skills_installs_one_skill_and_returns_metadata(tmp_path: Path) -> None:
    skill = _skill()
    target = tmp_path / "codex-skills"

    results = skills_module.install_skills(
        {skill.name: skill},
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
    one = _skill("untaped-one")
    two = _skill("untaped-two")
    project = tmp_path / "project"
    project.mkdir()
    project_root = project.resolve()

    results = skills_module.install_skills(
        {one.name: one, two.name: two},
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


def _install_local_from_cwd(skill: PackagedSkill) -> list[skills_module.SkillInstallResult]:
    return skills_module.install_skills(
        {skill.name: skill},
        [skill.name],
        stdin=False,
        all_skills=False,
        target=SkillInstallTarget.codex,
        force=False,
        scope=SkillInstallScope.local,
        project_dir=None,
        target_dir=None,
    )


def test_local_install_without_project_dir_uses_the_git_checkout_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    run_git(["init", "-q", str(repo)], timeout=30)
    nested = repo / "sub"
    nested.mkdir()
    monkeypatch.chdir(nested)

    [result] = _install_local_from_cwd(_skill())

    assert result.root == repo.resolve() / ".agents" / "skills"


def test_local_install_outside_a_checkout_uses_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = tmp_path / "plain"
    here.mkdir()
    monkeypatch.chdir(here)

    [result] = _install_local_from_cwd(_skill())

    assert result.root == here.resolve() / ".agents" / "skills"


def test_local_install_without_git_still_finds_the_checkout_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "sub").mkdir()
    monkeypatch.chdir(repo / "sub")
    monkeypatch.setattr("shutil.which", lambda *_, **__: None)

    [result] = _install_local_from_cwd(_skill())

    assert result.root == repo.resolve() / ".agents" / "skills"


def test_install_skills_later_conflict_aborts_before_earlier_install(tmp_path: Path) -> None:
    one = _skill("untaped-one")
    two = _skill("untaped-two")
    target = tmp_path / "codex-skills"
    target.joinpath("untaped-two").mkdir(parents=True)

    with pytest.raises(ConfigError, match="skill already exists: untaped-two"):
        skills_module.install_skills(
            {one.name: one, two.name: two},
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
    skill = _skill()

    with pytest.raises((ConfigError, UsageError), match=message):
        skills_module.install_skills(
            {skill.name: skill},
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
    skill = _skill()
    target = tmp_path / "codex-skills"
    installed = target / "untaped-demo"
    installed.mkdir(parents=True)
    installed.joinpath("SKILL.md").write_text("old\n")
    installed.joinpath("STALE.md").write_text("stale\n")

    results = skills_module.install_skills(
        {skill.name: skill},
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
    assert installed.joinpath("SKILL.md").read_bytes() == skill.stub_files()["SKILL.md"]
    assert not installed.joinpath("STALE.md").exists()
    assert not list(target.glob(".untaped-demo.backup-*"))


def test_install_skills_force_restores_existing_when_final_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = _skill()
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
            {skill.name: skill},
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


def test_checkout_root_is_the_git_ancestor_or_none(tmp_path: Path) -> None:
    """The walk the lint repo file shares with ``--scope local``, exported from the SDK."""
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    nested = tmp_path / "repo" / "a" / "b"
    nested.mkdir(parents=True)
    assert checkout_root(nested) == (tmp_path / "repo").resolve()
    plain = tmp_path / "plain"
    plain.mkdir()
    assert checkout_root(plain) is None


def test_checkout_root_finds_a_worktree_by_its_git_file_without_running_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "worktree"
    (worktree / "sub").mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/worktree\n")
    monkeypatch.setattr("shutil.which", lambda *_, **__: None)
    assert checkout_root(worktree / "sub") == worktree.resolve()


def test_updating_a_skill_no_plugin_ships_names_the_plugin_as_missing(tmp_path: Path) -> None:
    installed = skills_module.InstalledSkill(
        name="untaped-gone",
        target="codex",
        scope=SkillInstallScope.global_,
        path=tmp_path / "untaped-gone",
        state=skills_module.SkillState.orphaned,
    )
    with pytest.raises(ConfigError, match="no plugin installed here ships this skill"):
        skills_module.update_installed_skill({}, installed)
