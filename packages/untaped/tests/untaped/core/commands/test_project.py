"""``untaped project init|list``: make a folder a project, list what the home records."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from untaped.runtime import bootstrap
from untaped.sdk.storage import _refs as refs
from untaped.sdk.testing import CliResult, invoke_cli, make_project

pytestmark = pytest.mark.usefixtures("home_run")


def _cli(*args: str) -> CliResult:
    root = bootstrap.build_root_app(candidates=[])
    return invoke_cli(root.meta, list(args))


def test_init_makes_the_current_folder_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "PROJ-123"
    folder.mkdir()
    monkeypatch.chdir(folder)
    result = _cli("project", "init", "--format", "json")
    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert row == {
        "name": "PROJ-123",
        "action": "created",
        "path": str(folder),
        "id": row["id"],
        "detail": f"{folder} is a project (id {row['id']})",
    }
    assert f"{folder} is a project (id {row['id']})" in result.stderr
    again = _cli("project", "init", str(folder), "--format", "json")
    assert again.exit_code == 0
    assert json.loads(again.stdout)[0]["action"] == "unchanged"
    assert f"{folder} is already a project" in again.stderr


def test_init_refuses_inside_another_project_with_exit_2(tmp_path: Path) -> None:
    outer = make_project(tmp_path / "PROJ")
    result = _cli("project", "init", str(outer.root / "sub"))
    assert result.exit_code == 2
    assert f"{outer.root} is already a project; projects don't nest" in result.stderr


def test_list_shows_each_project_its_state_and_the_current_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = make_project(tmp_path / "PROJ-1")
    gone = make_project(tmp_path / "PROJ-2")
    shutil.rmtree(gone.root)
    (refs.REFS.place / "project-ffffffffffff.json").write_text("{", encoding="utf-8")
    monkeypatch.chdir(here.root)
    result = _cli("project", "list", "--format", "json")
    assert result.exit_code == 0, result.output
    rows = {row["name"]: row for row in json.loads(result.stdout)}
    assert rows["PROJ-1"] == {
        "name": "PROJ-1",
        "path": str(here.root),
        "state": "present",
        "current": True,
        "id": here.id,
        "holds": {},
    }
    assert rows["PROJ-2"]["state"] == "missing" and not rows["PROJ-2"]["current"]
    assert rows["PROJ-2"]["detail"] == f"{gone.root} is gone"
    unknown = rows[""]
    assert unknown["state"] == "unknown" and "id" not in unknown
    assert unknown["detail"].endswith("(unreadable (it is not valid JSON))")


def test_list_still_lists_from_a_folder_with_a_damaged_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = make_project(tmp_path / "PROJ-1")
    damaged = tmp_path / "PROJ-2"
    (damaged / ".untaped").mkdir(parents=True)
    (damaged / ".untaped" / "project.json").write_text("{", encoding="utf-8")
    monkeypatch.chdir(damaged)
    result = _cli("project", "list", "--format", "json")
    assert result.exit_code == 0, result.output
    assert "project marker" in result.stderr
    assert [row["path"] for row in json.loads(result.stdout)] == [str(other.root)]


def test_list_with_no_projects_says_how_to_make_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _cli("project", "list")
    assert result.exit_code == 0
    assert "Make one with `untaped project init`" in result.stderr + result.stdout


def test_list_never_repairs_a_moved_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path / "PROJ")
    moved = tmp_path / "moved"
    project.root.rename(moved)
    monkeypatch.chdir(moved)
    result = _cli("project", "list", "-q", "--format", "json")
    assert result.exit_code == 0
    assert f"project moved ({moved}) moved from {project.root}" in result.stderr  # kept under -q
    [row] = json.loads(result.stdout)
    assert (row["path"], row["state"]) == (str(project.root), "missing")
