"""Stub skills end to end: install, the per-run check, ``skills update``, ``status`` and doctor.

A newer run refreshes the home folder's stubs, an older one leaves them
alone, an edited stub is the user's, and a project's own skills are never
written by a run. Copies from 11.0 convert to stubs once, kept as backups.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from cyclopts import App

from test_management.support import age_stub, asset, make_spec, write_config
from untaped import bootstrap, skill_stubs
from untaped.management.skills import composed_skills
from untaped.plugins.registry import PluginSpec
from untaped.sdk import SkillAsset
from untaped.skill_stubs import STUB_MARKER
from untaped.skills import (
    SkillInstallScope,
    SkillInstallTarget,
    SkillKind,
    SkillState,
    find_installed_skills,
    skill_roots,
    update_installed_skill,
)
from untaped.testing import CliInvoker, CliResult, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

_COPY_MARKER = ".untaped-skill.json"


@pytest.fixture
def run_as(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Set the untaped version this test's runs report (11.1.0 to start)."""
    version = ["11.1.0"]
    monkeypatch.setattr(skill_stubs, "running_untaped", lambda: version[0])

    def set_version(value: str) -> None:
        version[0] = value

    return set_version


@pytest.fixture(autouse=True)
def _outside_any_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)


def _stub_spec(description: str = "Demo things: widgets and gadgets.") -> PluginSpec:
    return make_spec("demo", skills=(SkillAsset(name="untaped-demo", description=description),))


def _root(spec: PluginSpec | None = None) -> App:
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
    write_config(config, f"profiles:\n  default:\n    skills: {{updates: '{mode}'}}\n")


def _install_copy(tmp_path: Path, *, target: str = "codex", where: list[str] | None = None) -> Path:
    """Install an 11.0-style copy of ``untaped-demo`` (with references)."""
    copy = asset(tmp_path, "untaped-demo")
    assert copy.source is not None
    copy.source.joinpath("references").mkdir()
    copy.source.joinpath("references", "commands.md").write_text("# Commands\n", encoding="utf-8")
    root = _root(make_spec("demo", skills=(copy,)))
    result = _run(root, "skills", "install", "demo", "--target", target, *(where or []))
    assert result.exit_code == 0, result.output
    return copy.source


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
    assert "`untaped agents demo`" in text
    marker = _marker(stub)
    assert marker["format"] == 1
    assert (marker["untaped"], marker["plugin"], marker["plugin_version"]) == (
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


@pytest.mark.usefixtures("run_as")
def test_an_old_marker_scan_never_sees_a_stub() -> None:
    # untaped 11.0 counts only directories with the copy marker, so it never
    # reports a stub as outdated or writes a copy back over one (guardrail 4).
    assert _run(_root(), "skills", "install", "demo", "--target", "all").exit_code == 0
    for target in ("codex", "claude"):
        assert not (_home(target=target) / _COPY_MARKER).exists()
    roots = [root.root for root in skill_roots(project_dir=None)]
    assert [path for root in roots for path in root.glob(f"*/{_COPY_MARKER}")] == []


@pytest.mark.usefixtures("run_as")
def test_skills_list_shows_a_stub_s_kind_and_template_source(tmp_path: Path) -> None:
    copy = asset(tmp_path, "untaped-other")
    spec = make_spec("demo", skills=(SkillAsset(name="untaped-demo", description="Demo."),))
    root = bootstrap.build_root_app(
        candidates=(plugin_candidate(spec), plugin_candidate(make_spec("other", skills=(copy,))))
    )
    rows = {
        row["name"]: row for row in json.loads(_run(root, "skills", "list", "-f", "json").stdout)
    }
    assert (rows["untaped-demo"]["kind"], rows["untaped-demo"]["source"]) == (
        "stub",
        "untaped:stub/1",
    )
    assert (rows["untaped-other"]["kind"], rows["untaped-other"]["source"]) == (
        "copy",
        str(copy.source),
    )
    assert (rows["untaped"]["kind"], rows["untaped"]["source"]) == ("stub", "untaped:stub/1")


def test_core_s_own_skill_is_a_stub() -> None:
    (core,) = bootstrap.SHELL_SPEC.skills
    assert core.source is None


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
    assert "`untaped agents demo`" in (_home() / "SKILL.md").read_text(encoding="utf-8")
    assert _marker(_home())["untaped"] == "11.1.0"


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
    assert _marker(_home())["untaped"] == "11.3.0"


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
    assert [
        (row["name"], row["kind"], row["state"]) for row in rows if row["name"] == "untaped-demo"
    ] == [("untaped-demo", "stub", "edited")]


@pytest.mark.parametrize(
    ("args", "code"),
    [(("demo", "--no-such-flag"), 2), (("config", "set", "ui.theme", "default", "--dry-run"), 0)],
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


# conversion of 11.0 copies


def test_auto_converts_an_old_copy_once_and_keeps_a_backup(
    tmp_path: Path, run_as: Callable[[str], None]
) -> None:
    _install_copy(tmp_path, target="all")
    result = _run(_root(), "demo", "--help")
    assert result.exit_code == 0, result.output
    assert result.stdout.count("converted") == 0  # stderr only
    assert (
        "converted 2 agent skills to stubs; the old copies are in ~/.untaped/backups/skills"
        in result.stderr
    )
    for target in ("codex", "claude"):
        stub = _home(target=target)
        assert sorted(path.name for path in stub.iterdir()) == [STUB_MARKER, "SKILL.md"]
        kept = Path.home() / ".untaped" / "backups" / "skills" / target / "untaped-demo"
        assert (kept / "references" / "commands.md").is_file()
        assert (kept / _COPY_MARKER).is_file()
    assert _run(_root(), "demo", "--help").stderr == ""


def test_the_conversion_line_shows_under_quiet(
    tmp_path: Path, run_as: Callable[[str], None]
) -> None:
    _install_copy(tmp_path)
    result = _run(_root(), "-q", "demo", "--help")
    assert "converted 1 agent skill to stubs" in result.stderr


def test_a_second_conversion_keeps_both_backups(
    tmp_path: Path, run_as: Callable[[str], None]
) -> None:
    _install_copy(tmp_path)
    _run(_root(), "demo", "--help")
    _install_copy(tmp_path / "again", where=["--force"])
    _run(_root(), "demo", "--help")
    kept = Path.home() / ".untaped" / "backups" / "skills" / "codex"
    assert sorted(path.name for path in kept.iterdir()) == ["untaped-demo", "untaped-demo-2"]


def test_warn_reports_an_old_copy(
    _isolated_config: Path, tmp_path: Path, run_as: Callable[[str], None]
) -> None:
    _mode(_isolated_config, "warn")
    _install_copy(tmp_path)
    result = _run(_root(), "demo", "--help")
    assert "warning: agent skills from an older untaped: untaped-demo" in result.stderr
    assert (_home() / _COPY_MARKER).is_file()


@pytest.mark.parametrize("version", ["11.0.0", "11.0.3", "11.1.0.dev2"])
def test_a_run_before_11_1_0_converts_no_copy(
    tmp_path: Path, run_as: Callable[[str], None], version: str
) -> None:
    _install_copy(tmp_path)
    run_as(version)
    result = _run(_root(), "demo", "--help")
    assert result.stderr == ""
    assert (_home() / _COPY_MARKER).is_file()


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


def test_a_project_s_old_copy_is_warned_about_and_not_converted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_as: Callable[[str], None]
) -> None:
    project = _project(tmp_path, monkeypatch)
    _install_copy(tmp_path, where=["--scope", "local"])
    result = _run(_root(), "demo", "--help")
    assert (
        "warning: copied skills from an older untaped in this project: untaped-demo"
        in result.stderr
    )
    assert "run `untaped skills update` here (or set skills.updates to off)" in result.stderr
    assert (project / ".agents" / "skills" / "untaped-demo" / _COPY_MARKER).is_file()


def test_a_local_install_made_from_home_counts_as_the_home_folder(
    monkeypatch: pytest.MonkeyPatch, run_as: Callable[[str], None]
) -> None:
    monkeypatch.chdir(Path.home())
    root = _root()
    assert _run(root, "skills", "install", "demo", "--scope", "local").exit_code == 0
    assert _marker(_home())["scope"] == "local"
    age_stub(_home(), untaped="11.0.0")
    assert _run(root, "demo", "--help").stderr == ""
    assert _marker(_home())["untaped"] == "11.1.0"


def test_a_home_that_is_a_git_repo_is_still_the_home_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_as: Callable[[str], None]
) -> None:
    (Path.home() / ".git").mkdir()
    sub = Path.home() / "notes"
    sub.mkdir()
    monkeypatch.chdir(sub)
    root = _install_then_age(run_as)
    assert _run(root, "demo", "--help").stderr == ""
    assert _marker(_home())["untaped"] == "11.1.0"


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
    assert _marker(_home())["untaped"] == "11.1.0"


def test_update_leaves_an_edited_stub_and_force_replaces_it(run_as: Callable[[str], None]) -> None:
    root = _root()
    assert _run(root, "skills", "install", "demo").exit_code == 0
    (_home() / "SKILL.md").write_text("mine\n", encoding="utf-8")
    result = _run(root, "skills", "update")
    assert "left untaped-demo: edited since it was installed; --force replaces it" in result.stderr
    assert (_home() / "SKILL.md").read_text(encoding="utf-8") == "mine\n"
    assert _run(root, "skills", "update", "demo", "--force").exit_code == 0
    assert "`untaped agents demo`" in (_home() / "SKILL.md").read_text(encoding="utf-8")


def test_update_refreshes_a_stub_an_older_untaped_wrote(run_as: Callable[[str], None]) -> None:
    root = _install_then_age(run_as)
    result = _run(root, "skills", "update", "-f", "json")
    assert [row["action"] for row in json.loads(result.stdout)] == ["updated"]
    assert "`untaped agents demo`" in (_home() / "SKILL.md").read_text(encoding="utf-8")


def test_update_converts_a_project_s_copy_after_a_dry_run_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_as: Callable[[str], None]
) -> None:
    project = _project(tmp_path, monkeypatch)
    _install_copy(tmp_path, where=["--scope", "local"])
    installed = project / ".agents" / "skills" / "untaped-demo"
    preview = _run(_root(), "skills", "update", "--dry-run", "-f", "json")
    assert [row["action"] for row in json.loads(preview.stdout)] == ["planned"]
    assert (installed / _COPY_MARKER).is_file()
    result = _run(_root(), "skills", "update", "-f", "json")
    assert [row["action"] for row in json.loads(result.stdout)] == ["updated"]
    assert sorted(path.name for path in installed.iterdir()) == [STUB_MARKER, "SKILL.md"]
    assert not (Path.home() / ".untaped" / "backups").exists()


def test_status_shows_kind_and_state(tmp_path: Path, run_as: Callable[[str], None]) -> None:
    _install_copy(tmp_path, target="claude")
    assert _run(_root(), "skills", "install", "demo", "--target", "codex", "--force").exit_code == 0
    rows = json.loads(_run(_root(), "skills", "status", "-f", "json").stdout)
    states = {
        (row["target"], row["kind"], row["state"]) for row in rows if row["name"] == "untaped-demo"
    }
    assert states == {("codex", "stub", "current"), ("claude", "copy", "outdated")}


# parallel first runs


def test_parallel_conversions_and_refreshes_both_succeed(
    tmp_path: Path, run_as: Callable[[str], None]
) -> None:
    _install_copy(tmp_path)
    skills = composed_skills(
        bootstrap.SHELL_SPEC, bootstrap.compose_root(candidates=(plugin_candidate(_stub_spec()),))
    )
    roots = skill_roots(project_dir=None)
    # Both runs found the copy before either converted it.
    first, second = (
        next(item for item in find_installed_skills(skills, roots) if item.name == "untaped-demo")
        for _ in range(2)
    )
    backups = Path.home() / ".untaped" / "backups" / "skills"
    update_installed_skill(skills, first, backup_root=backups)
    update_installed_skill(skills, second, backup_root=backups)
    assert sorted(path.name for path in _home().iterdir()) == [STUB_MARKER, "SKILL.md"]
    assert [path.name for path in (backups / "codex").iterdir()] == ["untaped-demo"]
    assert [path.name for path in _home().parent.iterdir()] == ["untaped-demo"]

    age_stub(_home(), untaped="11.0.0")
    first, second = (
        next(item for item in find_installed_skills(skills, roots) if item.name == "untaped-demo")
        for _ in range(2)
    )
    assert first.kind is SkillKind.stub and first.state is SkillState.outdated
    update_installed_skill(skills, first)
    update_installed_skill(skills, second)
    assert sorted(path.name for path in _home().iterdir()) == [STUB_MARKER, "SKILL.md"]
    assert _marker(_home())["untaped"] == "11.1.0"


def test_a_conversion_that_finds_a_parallel_run_s_stub_keeps_no_backup(
    tmp_path: Path, run_as: Callable[[str], None]
) -> None:
    _install_copy(tmp_path)
    skills = composed_skills(
        bootstrap.SHELL_SPEC, bootstrap.compose_root(candidates=(plugin_candidate(_stub_spec()),))
    )
    roots = skill_roots(project_dir=None)
    stale = next(
        item for item in find_installed_skills(skills, roots) if item.name == "untaped-demo"
    )
    backups = Path.home() / ".untaped" / "backups" / "skills"
    update_installed_skill(skills, stale, backup_root=backups)
    # A slower run that still believes it holds the copy converts the stub again.
    update_installed_skill(skills, stale, backup_root=backups)
    assert [path.name for path in (backups / "codex").iterdir()] == ["untaped-demo"]
    assert sorted(path.name for path in _home().iterdir()) == [STUB_MARKER, "SKILL.md"]


# doctor


def test_doctor_reports_a_leftover_copy_with_an_automatic_fix(
    tmp_path: Path, run_as: Callable[[str], None]
) -> None:
    _install_copy(tmp_path)
    rows = json.loads(_run(_root(), "doctor", "-f", "json").stdout)
    row = next(row for row in rows if row["check"] == "skills")
    assert row["status"] == "warn"
    assert "outdated copy:" in row["detail"]
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
    assert "edited stub:" in row["detail"]
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


def test_a_plugin_with_two_stubs_is_quarantined() -> None:
    spec = make_spec(
        "demo",
        skills=(
            SkillAsset(name="untaped-demo", description="One."),
            SkillAsset(name="untaped-demo-two", description="Two."),
        ),
    )
    result = bootstrap.compose_root(candidates=(plugin_candidate(spec),))
    assert [(item.name, item.reason) for item in result.quarantine] == [("demo", "bad-skill-asset")]


def test_install_targets_and_scopes_vocabulary_is_unchanged() -> None:
    assert [target.value for target in SkillInstallTarget] == ["codex", "claude", "all"]
    assert [scope.value for scope in SkillInstallScope] == ["global", "local"]
