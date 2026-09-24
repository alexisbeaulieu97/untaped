"""Tests for the root ``untaped skills …`` command group (Wave 1.4).

The root group lists the union of the shell plus every composed
capability's skills. ``install`` accepts short selectors (``demo`` for an
installed ID of ``untaped-demo``) while installed directories and markers
keep the full ``untaped-*`` ID.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from test_management.support import asset, compose, make_spec
from untaped import bootstrap
from untaped.management.skills import build_root_skills_app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _skills_app(tmp_path: Path, *names: str) -> object:
    specs = [
        make_spec(f"cap-{index}", skills=(asset(tmp_path, name),))
        for index, name in enumerate(names)
    ]
    result = compose(*specs)
    return build_root_skills_app(shell=bootstrap.SHELL_SPEC, result=result)


def test_list_unions_skills_across_capabilities(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-two", "untaped-one")
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "name"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["untaped-one", "untaped-two"]


def test_install_full_name(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "untaped-demo", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert (target / "untaped-demo" / "SKILL.md").is_file()


def test_install_short_selector_keeps_full_installed_id(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "demo", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "installed skill: untaped-demo" in result.output
    installed = target / "untaped-demo"
    assert installed.joinpath("SKILL.md").is_file()
    marker = json.loads(installed.joinpath(".untaped-skill.json").read_text())
    assert marker["name"] == "untaped-demo"


def test_exact_name_wins_over_short_expansion(tmp_path: Path) -> None:
    first = asset(tmp_path, "untaped-demo")
    second = asset(tmp_path, "demo")
    result_compose = compose(
        make_spec("cap-a", skills=(first,)),
        make_spec("cap-b", skills=(second,)),
    )
    app = build_root_skills_app(shell=bootstrap.SHELL_SPEC, result=result_compose)
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "demo", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert (target / "demo" / "SKILL.md").is_file()
    assert not (target / "untaped-demo").exists()


def test_install_short_via_stdin_keeps_full_id(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(
        app,  # type: ignore[arg-type]
        ["install", "--stdin", "--target-dir", str(target)],
        input="demo\n",
    )
    assert result.exit_code == 0, result.output
    assert (target / "untaped-demo" / "SKILL.md").is_file()


def test_install_all_keeps_full_ids(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one", "untaped-two")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "--all", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert (target / "untaped-one" / "SKILL.md").is_file()
    assert (target / "untaped-two" / "SKILL.md").is_file()


def test_install_unknown_short_names_typed_selector(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "missing", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "missing" in result.output
    assert not target.exists()


def test_install_without_selector_is_usage_error(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    result = CliInvoker().invoke(app, ["install"])  # type: ignore[arg-type]
    assert result.exit_code == 2
    assert "provide skill names, --stdin, or --all" in result.output


def test_install_multiple_selectors_are_rejected(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "demo", "--all", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 2
    assert "not more than one" in result.output
    assert not target.exists()


def test_skills_module_imports_no_private_helpers() -> None:
    from untaped.management import skills as skills_module

    source = Path(skills_module.__file__).read_text(encoding="utf-8")
    private = [
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == "untaped.skills"
        for alias in node.names
        if alias.name.startswith("_")
    ]
    assert private == []


def _install(app: object, *args: str) -> None:
    result = CliInvoker().invoke(app, ["install", *args])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a git project and run from a subdirectory of it."""
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    monkeypatch.chdir(project / "sub")
    return project


def _json(app: object, *args: str) -> list[dict[str, object]]:
    result = CliInvoker().invoke(app, [*args, "--format", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


def test_status_reports_state_of_global_and_project_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, monkeypatch)
    app = _skills_app(tmp_path, "untaped-one", "untaped-two")
    _install(app, "--all")
    _install(app, "one", "--scope", "local", "--target", "claude")
    Path.home().joinpath(".agents", "skills", "untaped-two", "SKILL.md").write_text("old\n")

    rows = _json(app, "status")

    assert [(row["name"], row["scope"], row["target"], row["state"]) for row in rows] == [
        ("untaped-one", "global", "codex", "current"),
        ("untaped-two", "global", "codex", "outdated"),
        ("untaped-one", "local", "claude", "current"),
    ]
    assert rows[2]["target_path"] == str(project / ".claude" / "skills" / "untaped-one")


def test_status_ignores_hand_made_skills_and_flags_unshipped_ones(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one")
    _install(app, "one")
    root = Path.home() / ".agents" / "skills"
    (root / "mine").mkdir()
    (root / "mine" / "SKILL.md").write_text("mine\n")
    gone = _skills_app(tmp_path, "untaped-gone")
    _install(gone, "gone")

    rows = _json(app, "status")

    assert [(row["name"], row["state"]) for row in rows] == [
        ("untaped-gone", "orphaned"),
        ("untaped-one", "current"),
    ]


def test_status_check_exits_3_only_when_stale(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one")
    _install(app, "one")
    assert CliInvoker().invoke(app, ["status", "--check"]).exit_code == 0  # type: ignore[arg-type]
    Path.home().joinpath(".agents", "skills", "untaped-one", "SKILL.md").write_text("old\n")
    assert CliInvoker().invoke(app, ["status", "--check"]).exit_code == 3  # type: ignore[arg-type]


def test_update_refreshes_only_outdated_installs_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, monkeypatch)
    app = _skills_app(tmp_path, "untaped-one", "untaped-two")
    _install(app, "--all", "--scope", "local")
    local = project / ".agents" / "skills"
    local.joinpath("untaped-one", "SKILL.md").write_text("old\n")

    rows = _json(app, "update")

    assert [(row["name"], row["scope"], row["action"]) for row in rows] == [
        ("untaped-one", "local", "updated"),
    ]
    source = tmp_path / "skill-sources" / "untaped-one" / "SKILL.md"
    assert local.joinpath("untaped-one", "SKILL.md").read_text() == source.read_text()
    assert local.joinpath("untaped-one", ".untaped-skill.json").is_file()
    assert not Path.home().joinpath(".agents", "skills", "untaped-one").exists()


def test_update_selected_names_leaves_others(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one", "untaped-two")
    _install(app, "--all")
    root = Path.home() / ".agents" / "skills"
    for name in ("untaped-one", "untaped-two"):
        root.joinpath(name, "SKILL.md").write_text("old\n")

    rows = _json(app, "update", "two")

    assert [(row["name"], row["action"]) for row in rows] == [("untaped-two", "updated")]
    assert root.joinpath("untaped-one", "SKILL.md").read_text() == "old\n"


def test_update_dry_run_changes_nothing(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one")
    _install(app, "one")
    installed = Path.home() / ".agents" / "skills" / "untaped-one" / "SKILL.md"
    installed.write_text("old\n")

    rows = _json(app, "update", "--dry-run")

    assert [row["action"] for row in rows] == ["planned"]
    assert installed.read_text() == "old\n"


def test_update_named_current_skill_is_unchanged(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one")
    _install(app, "one")
    assert [row["action"] for row in _json(app, "update", "one")] == ["unchanged"]


def test_update_unknown_installed_name_fails(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one")
    _install(app, "one")
    result = CliInvoker().invoke(app, ["update", "two"])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "installed skill not found: 'two'; known: untaped-one" in result.output


def test_remove_selected_skill_from_every_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, monkeypatch)
    app = _skills_app(tmp_path, "untaped-one", "untaped-two")
    _install(app, "--all", "--target", "all")
    _install(app, "one", "--scope", "local")

    rows = _json(app, "remove", "one", "--yes")

    assert sorted((row["target"], row["scope"], row["action"]) for row in rows) == [
        ("claude", "global", "deleted"),
        ("codex", "global", "deleted"),
        ("codex", "local", "deleted"),
    ]
    assert not (project / ".agents" / "skills" / "untaped-one").exists()
    assert not Path.home().joinpath(".claude", "skills", "untaped-one").exists()
    assert Path.home().joinpath(".claude", "skills", "untaped-two").is_dir()


def test_remove_filters_by_target_and_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, monkeypatch)
    app = _skills_app(tmp_path, "untaped-one")
    _install(app, "one", "--target", "all")
    _install(app, "one", "--target", "all", "--scope", "local")

    rows = _json(app, "remove", "one", "--target", "codex", "--scope", "local", "--yes")

    assert [row["target_path"] for row in rows] == [
        str(project / ".agents" / "skills" / "untaped-one")
    ]
    assert (project / ".claude" / "skills" / "untaped-one").is_dir()
    assert Path.home().joinpath(".agents", "skills", "untaped-one").is_dir()


def test_remove_dry_run_and_hand_made_skills_are_kept(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one")
    _install(app, "one")
    mine = Path.home() / ".agents" / "skills" / "mine"
    mine.mkdir()

    rows = _json(app, "remove", "--all", "--dry-run")

    assert [(row["name"], row["action"]) for row in rows] == [("untaped-one", "planned")]
    assert Path.home().joinpath(".agents", "skills", "untaped-one").is_dir()
    assert _json(app, "remove", "--all", "--yes")[0]["action"] == "deleted"
    assert mine.is_dir()


def test_remove_requires_yes_when_not_interactive(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one")
    _install(app, "one")
    result = CliInvoker().invoke(app, ["remove", "one"])  # type: ignore[arg-type]
    assert result.exit_code == 2
    assert "--yes" in result.output
    assert Path.home().joinpath(".agents", "skills", "untaped-one").is_dir()


def test_remove_without_selector_is_usage_error(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one")
    result = CliInvoker().invoke(app, ["remove"])  # type: ignore[arg-type]
    assert result.exit_code == 2
    assert "provide skill names, --stdin, or --all" in result.output
