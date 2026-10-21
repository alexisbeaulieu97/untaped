"""Tests for the folder's requirement on untaped and the ``nearest_pyproject`` walk under it.

The requirement is read from the nearest ``pyproject.toml``'s standard
tables only, never above the farther of the checkout root and the project
root, and a malformed file or entry is no requirement at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.agents import untaped_requirement
from untaped.sdk import nearest_pyproject
from untaped.testing import make_project


def _project(folder: Path, text: str, *, git: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if git:
        (folder / ".git").mkdir(exist_ok=True)
    (folder / "pyproject.toml").write_text(text, encoding="utf-8")
    return folder


@pytest.mark.parametrize(
    "text",
    [
        '[project]\nname = "app"\ndependencies = ["httpx", "untaped[jira]==11.1.*"]\n',
        '[project]\nname = "app"\n[project.optional-dependencies]\nops = ["Untaped>=11.1"]\n',
        '[dependency-groups]\ndev = ["pytest", {include-group = "x"}, "untaped~=11.1"]\n',
    ],
    ids=["dependencies", "an extra", "a dependency group"],
)
def test_the_requirement_is_found_in_any_standard_table(tmp_path: Path, text: str) -> None:
    found = untaped_requirement(_project(tmp_path / "app", text))
    assert found is not None
    assert found.file == tmp_path / "app" / "pyproject.toml"
    assert found.requirement.name.lower() == "untaped"
    assert not found.satisfied_by("10.0.0")
    assert found.satisfied_by("11.1.0")


def test_the_nearest_file_wins_even_when_it_names_no_untaped(tmp_path: Path) -> None:
    outer = _project(tmp_path / "repo", '[project]\nname = "o"\ndependencies = ["untaped==1"]\n')
    inner = _project(outer / "sub", '[project]\nname = "i"\ndependencies = ["httpx"]\n', git=False)
    assert untaped_requirement(inner) is None
    assert nearest_pyproject(inner) == inner / "pyproject.toml"


def test_the_walk_climbs_to_the_checkout_root_and_never_above_it(tmp_path: Path) -> None:
    _project(tmp_path, '[project]\nname = "stray"\ndependencies = ["untaped==1"]\n', git=False)
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    work = checkout / "src" / "pkg"
    work.mkdir(parents=True)
    assert nearest_pyproject(work) is None
    assert untaped_requirement(work) is None
    (checkout / "pyproject.toml").write_text('[project]\nname = "c"\n', encoding="utf-8")
    assert nearest_pyproject(work) == checkout / "pyproject.toml"


def test_outside_a_checkout_only_the_start_folder_is_read(tmp_path: Path) -> None:
    _project(tmp_path, '[project]\nname = "up"\ndependencies = ["untaped==1"]\n', git=False)
    below = tmp_path / "below"
    below.mkdir()
    assert nearest_pyproject(below) is None


def test_a_requirement_with_no_version_admits_every_untaped(tmp_path: Path) -> None:
    found = untaped_requirement(_project(tmp_path, '[dependency-groups]\ndev = ["untaped[all]"]\n'))
    assert found is not None
    assert found.satisfied_by("11.3.0")


@pytest.mark.parametrize(
    "text",
    [
        "not = [toml",
        '[project]\ndependencies = ["untaped >>>= nonsense"]\n',
        '[project]\ndependencies = "untaped==1"\n',
        '[dependency-groups]\ndev = "untaped==1"\n',
        "project = 3\n",
        "[project]\ndependencies = ['untaped>=99; \"dev\" in dependency_groups']\n",
        "[project]\ndependencies = ['untaped>=99; python_version ~= \"3\"']\n",
    ],
    ids=[
        "broken toml",
        "bad requirement",
        "not a list",
        "group not a list",
        "not a table",
        "a marker naming an unknown variable",
        "a marker comparing what cannot be compared",
    ],
)
def test_a_malformed_file_or_entry_is_no_requirement(tmp_path: Path, text: str) -> None:
    assert untaped_requirement(_project(tmp_path, text)) is None


def test_no_pyproject_is_no_requirement(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert untaped_requirement(tmp_path) is None


_PIN = '[project]\nname = "p"\ndependencies = ["untaped==11.1.*"]\n'


def test_in_a_project_outside_a_checkout_the_walk_reaches_the_project(tmp_path: Path) -> None:
    project = _project(tmp_path / "PROJ", _PIN, git=False)
    make_project(project)
    notes = project / "notes"
    notes.mkdir()
    assert nearest_pyproject(notes) == project / "pyproject.toml"
    assert untaped_requirement(notes) is not None


def test_from_a_worktree_in_a_project_the_walk_reaches_the_project(tmp_path: Path) -> None:
    # As uv finds PROJ's project from PROJ/api: the project is the farther bound.
    project = _project(tmp_path / "PROJ", _PIN, git=False)
    make_project(project)
    src = project / "api" / "src"
    src.mkdir(parents=True)
    (project / "api" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    assert nearest_pyproject(src) == project / "pyproject.toml"


def test_in_a_project_inside_a_checkout_the_walk_reaches_the_checkout(tmp_path: Path) -> None:
    app = _project(tmp_path / "app", _PIN)
    ops = app / "ops"
    make_project(ops)
    assert nearest_pyproject(ops) == app / "pyproject.toml"


def test_the_walk_never_climbs_above_both_roots(tmp_path: Path) -> None:
    _project(tmp_path, _PIN, git=False)
    project = tmp_path / "PROJ"
    make_project(project)
    (project / "notes").mkdir()
    assert nearest_pyproject(project / "notes") is None
