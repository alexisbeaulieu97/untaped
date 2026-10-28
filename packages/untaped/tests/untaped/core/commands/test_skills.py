"""Tests for the root ``untaped skills …`` command group.

The root group lists the union of core's plus every composed
plugin's skills. ``install`` accepts short selectors (``demo`` for an
installed ID of ``untaped-demo``) while installed directories and markers
keep the full ``untaped-*`` ID.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from core_test_support.commands.support import age_stub, asset, make_spec, root_group
from untaped.core.commands import skills as skills_cli
from untaped.core.skill_stubs import STUB_MARKER
from untaped.sdk.errors import ConfigError
from untaped.sdk.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _skills_app(*names: str) -> object:
    specs = [
        make_spec(f"plugin-{index}", skills=(asset(name),)) for index, name in enumerate(names)
    ]
    return root_group("skills", *specs)


def test_list_unions_skills_across_plugins() -> None:
    app = _skills_app("untaped-two", "untaped-one")
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "name"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    # Core's own skill is listed with the plugins'.
    assert result.output.splitlines() == ["untaped", "untaped-one", "untaped-two"]


def test_install_full_name(tmp_path: Path) -> None:
    app = _skills_app("untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "untaped-demo", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert (target / "untaped-demo" / "SKILL.md").is_file()


def test_install_short_selector_keeps_full_installed_id(tmp_path: Path) -> None:
    app = _skills_app("untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "demo", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "installed skill: untaped-demo" in result.output
    installed = target / "untaped-demo"
    assert installed.joinpath("SKILL.md").is_file()
    marker = json.loads(installed.joinpath(STUB_MARKER).read_text())
    assert marker["name"] == "untaped-demo"


def test_exact_name_wins_over_short_expansion(tmp_path: Path) -> None:
    first = asset("untaped-demo")
    second = asset("demo")
    app = root_group(
        "skills",
        make_spec("plugin-a", skills=(first,)),
        make_spec("plugin-b", skills=(second,)),
    )
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "demo", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert (target / "demo" / "SKILL.md").is_file()
    assert not (target / "untaped-demo").exists()


def test_install_short_via_stdin_keeps_full_id(tmp_path: Path) -> None:
    app = _skills_app("untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(
        app,  # type: ignore[arg-type]
        ["install", "--stdin", "--target-dir", str(target)],
        input="demo\n",
    )
    assert result.exit_code == 0, result.output
    assert (target / "untaped-demo" / "SKILL.md").is_file()


def test_install_reads_the_rows_skills_list_pipes(tmp_path: Path) -> None:
    app = _skills_app("untaped-one", "untaped-two")
    listed = CliInvoker().invoke(app, ["list", "--format", "pipe"])  # type: ignore[arg-type]
    rows = [line for line in listed.output.splitlines() if '"untaped-two"' in line]
    target = tmp_path / "skills"
    result = CliInvoker().invoke(
        app,  # type: ignore[arg-type]
        ["install", "--stdin", "--target-dir", str(target)],
        input="\n".join(rows) + "\n",
    )
    assert result.exit_code == 0, result.output
    assert [path.name for path in target.iterdir()] == ["untaped-two"]


def test_install_refuses_names_and_stdin_together(tmp_path: Path) -> None:
    app = _skills_app("untaped-demo")
    result = CliInvoker().invoke(
        app,  # type: ignore[arg-type]
        ["install", "demo", "--stdin", "--target-dir", str(tmp_path)],
        input="demo\n",
    )
    assert result.exit_code == 2
    assert "not both" in result.output


def test_install_all_keeps_full_ids(tmp_path: Path) -> None:
    app = _skills_app("untaped-one", "untaped-two")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "--all", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert (target / "untaped-one" / "SKILL.md").is_file()
    assert (target / "untaped-two" / "SKILL.md").is_file()


def test_install_unknown_short_names_typed_selector(tmp_path: Path) -> None:
    app = _skills_app("untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "missing", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "missing" in result.output
    assert not target.exists()


def test_install_without_selector_is_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _skills_app("untaped-demo")
    result = CliInvoker().invoke(app, ["install"])  # type: ignore[arg-type]
    assert result.exit_code == 2
    assert "provide skill names, --stdin, or --all" in result.output
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    structured = CliInvoker().invoke(app, ["install"])  # type: ignore[arg-type]
    assert json.loads(structured.stderr)["system"] == "untaped"


def test_install_multiple_selectors_are_rejected(tmp_path: Path) -> None:
    app = _skills_app("untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "demo", "--all", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 2
    assert "not more than one" in result.output
    assert not target.exists()


def test_skills_module_imports_no_private_helpers() -> None:
    from untaped.core.commands import skills as skills_module

    source = Path(skills_module.__file__).read_text(encoding="utf-8")
    private = [
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == "untaped.core.skills"
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
    app = _skills_app("untaped-one", "untaped-two")
    _install(app, "--all")
    _install(app, "one", "--scope", "local", "--target", "claude")
    age_stub(Path.home() / ".agents" / "skills" / "untaped-two")

    rows = _json(app, "status")

    assert [(row["name"], row["scope"], row["target"], row["state"]) for row in rows] == [
        ("untaped", "global", "codex", "current"),
        ("untaped-one", "global", "codex", "current"),
        ("untaped-two", "global", "codex", "outdated"),
        ("untaped-one", "local", "claude", "current"),
    ]
    assert rows[3]["target_path"] == str(project / ".claude" / "skills" / "untaped-one")


def test_status_ignores_hand_made_skills_and_flags_unshipped_ones() -> None:
    _install(_skills_app("untaped-gone"), "gone")
    # Built last: the run reads the composition the newest root built.
    app = _skills_app("untaped-one")
    _install(app, "one")
    root = Path.home() / ".agents" / "skills"
    (root / "mine").mkdir()
    (root / "mine" / "SKILL.md").write_text("mine\n")

    rows = _json(app, "status")

    assert [(row["name"], row["state"]) for row in rows] == [
        ("untaped-gone", "orphaned"),
        ("untaped-one", "current"),
    ]


def test_status_check_exits_3_only_when_stale() -> None:
    app = _skills_app("untaped-one")
    _install(app, "one")
    assert CliInvoker().invoke(app, ["status", "--check"]).exit_code == 0  # type: ignore[arg-type]
    age_stub(Path.home() / ".agents" / "skills" / "untaped-one")
    assert CliInvoker().invoke(app, ["status", "--check"]).exit_code == 3  # type: ignore[arg-type]


def test_update_refreshes_only_outdated_installs_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, monkeypatch)
    app = _skills_app("untaped-one", "untaped-two")
    _install(app, "--all", "--scope", "local")
    local = project / ".agents" / "skills"
    age_stub(local / "untaped-one")

    rows = _json(app, "update")

    assert [(row["name"], row["scope"], row["action"]) for row in rows] == [
        ("untaped-one", "local", "updated"),
    ]
    assert "`untaped docs plugin-0`" in local.joinpath("untaped-one", "SKILL.md").read_text()
    assert local.joinpath("untaped-one", STUB_MARKER).is_file()
    assert not Path.home().joinpath(".agents", "skills", "untaped-one").exists()


def test_update_selected_names_leaves_others() -> None:
    app = _skills_app("untaped-one", "untaped-two")
    _install(app, "--all")
    root = Path.home() / ".agents" / "skills"
    for name in ("untaped-one", "untaped-two"):
        age_stub(root / name)

    rows = _json(app, "update", "two")

    assert [(row["name"], row["action"]) for row in rows] == [("untaped-two", "updated")]
    assert root.joinpath("untaped-one", "SKILL.md").read_text() == "older wording\n"


def test_update_reads_the_rows_status_pipes() -> None:
    app = _skills_app("untaped-one", "untaped-two")
    _install(app, "--all")
    root = Path.home() / ".agents" / "skills"
    for name in ("untaped-one", "untaped-two"):
        age_stub(root / name)
    listed = CliInvoker().invoke(app, ["status", "--format", "pipe"])  # type: ignore[arg-type]
    piped = [line for line in listed.stdout.splitlines() if '"untaped-two"' in line]

    result = CliInvoker().invoke(
        app,  # type: ignore[arg-type]
        ["update", "--stdin", "--format", "json"],
        input="\n".join(piped) + "\n",
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [(row["name"], row["action"]) for row in rows] == [("untaped-two", "updated")]
    assert root.joinpath("untaped-one", "SKILL.md").read_text() == "older wording\n"


def test_update_dry_run_changes_nothing() -> None:
    app = _skills_app("untaped-one")
    _install(app, "one")
    installed = Path.home() / ".agents" / "skills" / "untaped-one"
    age_stub(installed)

    rows = _json(app, "update", "--dry-run")

    assert [row["action"] for row in rows] == ["planned"]
    assert installed.joinpath("SKILL.md").read_text() == "older wording\n"


def test_update_named_current_skill_is_unchanged() -> None:
    app = _skills_app("untaped-one")
    _install(app, "one")
    assert [row["action"] for row in _json(app, "update", "one")] == ["unchanged"]


def test_update_keeps_a_failure_s_category_on_its_row(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _skills_app("untaped-one")
    _install(app, "one")
    age_stub(Path.home() / ".agents" / "skills" / "untaped-one")

    def broken(*_args: object, **_kwargs: object) -> None:
        raise ConfigError("could not update it", hint="check the permissions")

    monkeypatch.setattr(skills_cli, "update_installed_skill", broken)
    result = CliInvoker().invoke(app, ["update", "--format", "json"])  # type: ignore[arg-type]

    assert result.exit_code == 4
    [row] = json.loads(result.stdout)
    assert row["action"] == "failed"
    assert row["error"]["category"] == "config"
    assert row["error"]["hint"] == "check the permissions"
    [line] = [json.loads(line) for line in result.stderr.splitlines()]
    assert (line["item"], line["message"]) == ("untaped-one", "could not update it")


def test_update_named_unshipped_skill_says_no_installed_plugin_ships_it() -> None:
    _install(_skills_app("untaped-gone"), "gone")
    app = _skills_app("untaped-one")
    result = CliInvoker().invoke(app, ["update", "untaped-gone", "--format", "json"])  # type: ignore[arg-type]
    [row] = json.loads(result.stdout)
    assert row["action"] == "failed"
    assert "no plugin installed here ships this skill" in result.stderr


def test_update_unknown_installed_name_fails() -> None:
    app = _skills_app("untaped-one")
    _install(app, "one")
    result = CliInvoker().invoke(app, ["update", "two"])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "installed skill not found: 'two'; known: untaped-one" in result.output


def test_remove_selected_skill_from_every_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, monkeypatch)
    app = _skills_app("untaped-one", "untaped-two")
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
    app = _skills_app("untaped-one")
    _install(app, "one", "--target", "all")
    _install(app, "one", "--target", "all", "--scope", "local")

    rows = _json(app, "remove", "one", "--target", "codex", "--scope", "local", "--yes")

    assert [row["target_path"] for row in rows] == [
        str(project / ".agents" / "skills" / "untaped-one")
    ]
    assert (project / ".claude" / "skills" / "untaped-one").is_dir()
    assert Path.home().joinpath(".agents", "skills", "untaped-one").is_dir()


def test_remove_dry_run_and_hand_made_skills_are_kept() -> None:
    app = _skills_app("untaped-one")
    _install(app, "one")
    mine = Path.home() / ".agents" / "skills" / "mine"
    mine.mkdir()

    rows = _json(app, "remove", "--all", "--dry-run")

    assert [(row["name"], row["action"]) for row in rows] == [("untaped-one", "planned")]
    assert Path.home().joinpath(".agents", "skills", "untaped-one").is_dir()
    assert _json(app, "remove", "--all", "--yes")[0]["action"] == "deleted"
    assert mine.is_dir()


def test_remove_requires_yes_when_not_interactive() -> None:
    app = _skills_app("untaped-one")
    _install(app, "one")
    result = CliInvoker().invoke(app, ["remove", "one"])  # type: ignore[arg-type]
    assert result.exit_code == 2
    assert "--yes" in result.output
    assert Path.home().joinpath(".agents", "skills", "untaped-one").is_dir()


def test_remove_without_selector_is_usage_error() -> None:
    app = _skills_app("untaped-one")
    result = CliInvoker().invoke(app, ["remove"])  # type: ignore[arg-type]
    assert result.exit_code == 2
    assert "provide skill names, --stdin, or --all" in result.output
