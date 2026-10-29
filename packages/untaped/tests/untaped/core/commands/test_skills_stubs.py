"""Stub skills end to end: install, the per-run check, ``skills update``, ``status`` and doctor.

A newer run refreshes the home folder's stubs, an older one leaves them
alone, an edited stub is the user's, and a project's own skills are never
written by a run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from cyclopts import App

from core_test_support.commands.support import age_stub, make_plugin, write_config
from untaped.core import skill_stubs
from untaped.core.commands.skills import composed_skills
from untaped.core.skill_stubs import STUB_MARKER
from untaped.core.skills import (
    SkillInstallScope,
    SkillInstallTarget,
    SkillState,
    find_installed_skills,
    skill_roots,
    update_installed_skill,
)
from untaped.runtime import bootstrap
from untaped.sdk import Plugin
from untaped.sdk.storage import data_file
from untaped.sdk.testing import CliInvoker, CliResult, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


@pytest.fixture
def run_as(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Set the untaped version this test's runs report and stamp (11.1.0 to start)."""
    version = ["11.1.0"]
    monkeypatch.setattr(data_file, "written_by", lambda: version[0])

    def set_version(value: str) -> None:
        version[0] = value

    return set_version


@pytest.fixture(autouse=True)
def _outside_any_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)


def _stub_spec(description: str = "Demo things: widgets and gadgets.") -> type[Plugin]:
    return make_plugin("demo", skill=description)


def _root(spec: type[Plugin] | None = None) -> App:
    return bootstrap.build_root_app(candidates=(plugin_candidate(spec or _stub_spec()),))


def _run(root: App, *args: str) -> CliResult:
    return CliInvoker().invoke(root.meta, list(args))


def _home(name: str = "untaped-demo", target: str = "codex") -> Path:
    folder = ".agents" if target == "codex" else ".claude"
    return Path.home() / folder / "skills" / name


def _marker(path: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((path / STUB_MARKER).read_text(encoding="utf-8"))
    return data


def _mode(config: Path, mode: str) -> None:
    write_config(config, f"format_version: 2\nuntaped: {{skills: {{updates: '{mode}'}}}}\n")


# install


@pytest.mark.parametrize("target", ["codex", "claude"])
@pytest.mark.usefixtures("run_as")
def test_install_writes_a_stub_and_its_marker(target: str) -> None:
    result = _run(_root(), "skills", "install", "demo", "--target", target)
    assert result.exit_code == 0, result.output
    stub = _home(target=target)
    assert sorted(path.name for path in stub.iterdir()) == [STUB_MARKER, "SKILL.md"]
    text = (stub / "SKILL.md").read_text(encoding="utf-8")
    assert 'description: "Demo things: widgets and gadgets."' in text
    assert "`untaped docs demo`" in text
    marker = _marker(stub)
    assert marker["untaped"] == {"file": 1, "by": "11.1.0"}
    assert "format" not in marker
    assert (marker["untaped"]["by"], marker["plugin"], marker["plugin_version"]) == (
        "11.1.0",
        "untaped-demo",
        "",
    )
    assert marker["sha256"] == skill_stubs.digest({"SKILL.md": text.encode()})
    assert (marker["target"], marker["scope"]) == (target, "global")


@pytest.mark.usefixtures("run_as")
def test_install_local_writes_a_stub_in_the_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    result = _run(
        _root(), "skills", "install", "demo", "--scope", "local", "--project-dir", str(project)
    )
    assert result.exit_code == 0, result.output
    assert _marker(project / ".agents" / "skills" / "untaped-demo")["scope"] == "local"


def test_skills_list_shows_each_skill_s_name_and_description() -> None:
    rows = json.loads(_run(_root(), "skills", "list", "-f", "json").stdout)
    assert [(row["name"], row["description"]) for row in rows] == [
        ("untaped", composed_skills(bootstrap.compose_root(candidates=()))["untaped"].description),
        ("untaped-demo", "Demo things: widgets and gadgets."),
    ]


def test_core_s_own_skill_is_named_untaped_and_points_at_core() -> None:
    skills = composed_skills(bootstrap.compose_root(candidates=()))
    assert (skills["untaped"].plugin, skills["untaped"].distribution) == ("untaped", "untaped")


# the per-run check: home folder


def _install_then_age(run_as: Callable[[str], None]) -> App:
    root = _root()
    assert _run(root, "skills", "install", "demo").exit_code == 0
    age_stub(_home(), untaped="11.0.0")
    return root


def test_auto_refreshes_a_stub_an_older_untaped_wrote_silently(
    run_as: Callable[[str], None],
) -> None:
    root = _install_then_age(run_as)
    result = _run(root, "demo", "--help")
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert "`untaped docs demo`" in (_home() / "SKILL.md").read_text(encoding="utf-8")
    assert _marker(_home())["untaped"]["by"] == "11.1.0"


def test_warn_reports_a_stub_an_older_untaped_wrote(
    _isolated_config: Path, run_as: Callable[[str], None]
) -> None:
    _mode(_isolated_config, "warn")
    root = _install_then_age(run_as)
    result = _run(root, "demo", "--help")
    assert "warning: agent skills from an older untaped: untaped-demo" in result.stderr
    assert "run `untaped skills update`" in result.stderr
    assert (_home() / "SKILL.md").read_text(encoding="utf-8") == "older wording\n"


def test_off_does_nothing(_isolated_config: Path, run_as: Callable[[str], None]) -> None:
    _mode(_isolated_config, "off")
    root = _install_then_age(run_as)
    result = _run(root, "demo", "--help")
    assert result.stderr == ""
    assert (_home() / "SKILL.md").read_text(encoding="utf-8") == "older wording\n"


@pytest.mark.parametrize("mode", ["auto", "warn"])
def test_a_stub_a_newer_untaped_wrote_is_left_alone(
    _isolated_config: Path, run_as: Callable[[str], None], mode: str
) -> None:
    _mode(_isolated_config, mode)
    run_as("11.3.0")
    root = _root(_stub_spec("Newer wording."))
    assert _run(root, "skills", "install", "demo").exit_code == 0
    before = (_home() / "SKILL.md").read_bytes()
    run_as("11.1.0")
    result = _run(_root(), "demo", "--help")
    assert result.stderr == ""
    assert (_home() / "SKILL.md").read_bytes() == before
    assert _marker(_home())["untaped"]["by"] == "11.3.0"


def test_the_same_version_with_other_text_writes_nothing(run_as: Callable[[str], None]) -> None:
    # A dev checkout carries the last released number: its wording waits for the release.
    root = _root()
    assert _run(root, "skills", "install", "demo").exit_code == 0
    result = _run(_root(_stub_spec("Unreleased wording.")), "demo", "--help")
    assert result.stderr == ""
    assert "Unreleased" not in (_home() / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["auto", "warn"])
def test_an_edited_stub_is_never_rewritten_by_a_run(
    _isolated_config: Path, run_as: Callable[[str], None], mode: str
) -> None:
    _mode(_isolated_config, mode)
    root = _install_then_age(run_as)
    (_home() / "SKILL.md").write_text("my own notes\n", encoding="utf-8")
    result = _run(root, "demo", "--help")
    assert result.stderr == ""
    assert (_home() / "SKILL.md").read_text(encoding="utf-8") == "my own notes\n"
    rows = json.loads(_run(root, "skills", "status", "-f", "json").stdout)
    assert [(row["name"], row["state"]) for row in rows if row["name"] == "untaped-demo"] == [
        ("untaped-demo", "edited")
    ]


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("demo", "--no-such-flag"), 2),
        (("config", "set", "untaped.ui.theme", "default", "--dry-run"), 0),
    ],
    ids=["failed", "dry-run"],
)
def test_nothing_is_written_after_a_failed_or_previewing_command(
    run_as: Callable[[str], None], args: tuple[str, ...], code: int
) -> None:
    root = _install_then_age(run_as)
    result = _run(root, *args)
    assert result.exit_code == code, result.output
    assert "agent skills" not in result.stderr
    assert (_home() / "SKILL.md").read_text(encoding="utf-8") == "older wording\n"


# a project's own skills


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.chdir(project)
    return project


@pytest.mark.parametrize("mode", ["auto", "warn"])
def test_a_project_s_stub_is_never_written_or_warned_about(
    _isolated_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_as: Callable[[str], None],
    mode: str,
) -> None:
    _mode(_isolated_config, mode)
    project = _project(tmp_path, monkeypatch)
    root = _root()
    assert _run(root, "skills", "install", "demo", "--scope", "local").exit_code == 0
    stub = project / ".agents" / "skills" / "untaped-demo"
    age_stub(stub, untaped="11.0.0")
    result = _run(root, "demo", "--help")
    assert result.stderr == ""
    assert (stub / "SKILL.md").read_text(encoding="utf-8") == "older wording\n"


def test_a_local_install_made_from_home_counts_as_the_home_folder(
    monkeypatch: pytest.MonkeyPatch, run_as: Callable[[str], None]
) -> None:
    monkeypatch.chdir(Path.home())
    root = _root()
    assert _run(root, "skills", "install", "demo", "--scope", "local").exit_code == 0
    assert _marker(_home())["scope"] == "local"
    age_stub(_home(), untaped="11.0.0")
    assert _run(root, "demo", "--help").stderr == ""
    assert _marker(_home())["untaped"]["by"] == "11.1.0"


def test_a_home_that_is_a_git_repo_is_still_the_home_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_as: Callable[[str], None]
) -> None:
    (Path.home() / ".git").mkdir()
    sub = Path.home() / "notes"
    sub.mkdir()
    monkeypatch.chdir(sub)
    root = _install_then_age(run_as)
    assert _run(root, "demo", "--help").stderr == ""
    assert _marker(_home())["untaped"]["by"] == "11.1.0"


# skills update and status


def test_update_leaves_a_stub_a_newer_untaped_wrote_and_force_rewrites_it(
    run_as: Callable[[str], None],
) -> None:
    run_as("11.3.0")
    assert _run(_root(), "skills", "install", "demo").exit_code == 0
    run_as("11.1.0")
    result = _run(_root(), "skills", "update", "-f", "json")
    assert result.exit_code == 0, result.output
    assert "left untaped-demo: written by a newer untaped (11.3.0)" in result.stderr
    assert [row["action"] for row in json.loads(result.stdout)] == ["skipped"]
    forced = _run(_root(), "skills", "update", "--force", "-f", "json")
    assert [row["action"] for row in json.loads(forced.stdout)] == ["updated"]
    assert _marker(_home())["untaped"]["by"] == "11.1.0"


def test_update_leaves_an_edited_stub_and_force_replaces_it(run_as: Callable[[str], None]) -> None:
    root = _root()
    assert _run(root, "skills", "install", "demo").exit_code == 0
    (_home() / "SKILL.md").write_text("mine\n", encoding="utf-8")
    result = _run(root, "skills", "update")
    assert "left untaped-demo: edited since it was installed; --force replaces it" in result.stderr
    assert (_home() / "SKILL.md").read_text(encoding="utf-8") == "mine\n"
    assert _run(root, "skills", "update", "demo", "--force").exit_code == 0
    assert "`untaped docs demo`" in (_home() / "SKILL.md").read_text(encoding="utf-8")


def test_update_refreshes_a_stub_an_older_untaped_wrote(run_as: Callable[[str], None]) -> None:
    root = _install_then_age(run_as)
    result = _run(root, "skills", "update", "-f", "json")
    assert [row["action"] for row in json.loads(result.stdout)] == ["updated"]
    assert "`untaped docs demo`" in (_home() / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.usefixtures("run_as")
def test_status_shows_each_install_s_state() -> None:
    assert _run(_root(), "skills", "install", "demo", "--target", "all").exit_code == 0
    age_stub(_home(target="claude"), untaped="11.0.0")
    rows = json.loads(_run(_root(), "skills", "status", "-f", "json").stdout)
    states = {(row["target"], row["state"]) for row in rows if row["name"] == "untaped-demo"}
    assert states == {("codex", "current"), ("claude", "outdated")}


# parallel first runs


@pytest.mark.usefixtures("run_as")
def test_parallel_refreshes_both_succeed() -> None:
    assert _run(_root(), "skills", "install", "demo").exit_code == 0
    age_stub(_home(), untaped="11.0.0")
    skills = composed_skills(bootstrap.compose_root(candidates=(plugin_candidate(_stub_spec()),)))
    roots = skill_roots(project_dir=None)
    # Both runs found the outdated stub before either refreshed it.
    first, second = (
        next(item for item in find_installed_skills(skills, roots) if item.name == "untaped-demo")
        for _ in range(2)
    )
    assert first.state is SkillState.outdated
    update_installed_skill(skills, first)
    update_installed_skill(skills, second)
    assert sorted(path.name for path in _home().iterdir()) == [STUB_MARKER, "SKILL.md"]
    assert [path.name for path in _home().parent.iterdir()] == ["untaped-demo"]
    assert _marker(_home())["untaped"]["by"] == "11.1.0"


# doctor


def test_doctor_reports_an_outdated_stub_with_an_automatic_fix(
    run_as: Callable[[str], None],
) -> None:
    root = _install_then_age(run_as)
    rows = json.loads(_run(root, "doctor", "-f", "json").stdout)
    row = next(row for row in rows if row["check"] == "skills")
    assert row["status"] == "warn"
    assert "outdated skill:" in row["detail"]
    assert (row["fix"][-2:], row["automatic"]) == (["skills", "update"], True)


def test_doctor_reports_an_edited_stub_without_an_automatic_fix(
    run_as: Callable[[str], None],
) -> None:
    root = _root()
    assert _run(root, "skills", "install", "demo").exit_code == 0
    (_home() / "SKILL.md").write_text("mine\n", encoding="utf-8")
    row = next(
        row
        for row in json.loads(_run(root, "doctor", "-f", "json").stdout)
        if row["check"] == "skills"
    )
    assert row["status"] == "warn"
    assert "edited skill:" in row["detail"]
    assert "untaped skills update --force NAME" in row["detail"]
    assert (row["fix"], row["automatic"]) == (None, False)


def test_doctor_passes_a_stub_a_newer_untaped_wrote(run_as: Callable[[str], None]) -> None:
    run_as("11.3.0")
    assert _run(_root(), "skills", "install", "demo").exit_code == 0
    run_as("11.1.0")
    row = next(
        row
        for row in json.loads(_run(_root(), "doctor", "-f", "json").stdout)
        if row["check"] == "skills"
    )
    assert row["status"] == "pass"


# registry


def test_install_targets_and_scopes_vocabulary_is_unchanged() -> None:
    assert [target.value for target in SkillInstallTarget] == ["codex", "claude", "all"]
    assert [scope.value for scope in SkillInstallScope] == ["global", "local"]


def test_a_refresh_leaves_a_parallel_run_s_temporary_file(run_as: Callable[[str], None]) -> None:
    root = _install_then_age(run_as)
    foreign = _home() / f".SKILL.md.{'0' * 32}.untaped.tmp"
    foreign.write_text("another run's write\n", encoding="utf-8")
    age_stub(_home(), untaped="11.0.0")  # the marker's digest leaves the temp file out
    assert _run(root, "demo", "--help").stderr == ""
    assert foreign.is_file()
    assert _marker(_home())["untaped"]["by"] == "11.1.0"
