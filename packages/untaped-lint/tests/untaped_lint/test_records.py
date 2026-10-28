"""The repository's ``Tree``: which files it holds, and how a rule reads them."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped.sdk.testing import compose_with
from untaped_lint.api import FileLocation, Finding, Rules, Tree, finding, rule, target_of
from untaped_lint.application.tree_paths import tree_paths
from untaped_lint.testing import run, run_rule, tree

pytestmark = pytest.mark.usefixtures("fresh_composition")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")


@rule(severity="error")
def todo(files: Tree) -> Iterator[Finding]:
    """No TODO in a Python file."""
    for file in files.files("**/*.py"):
        for number, line in enumerate(file.lines, start=1):
            if "TODO" in line:
                yield finding(
                    todo,
                    target_of(file),
                    "a TODO",
                    location=FileLocation(path=file.path, line=number),
                )


class TodoRules(Rules):
    RULES = (todo,)


def test_a_checkout_holds_tracked_and_untracked_files_but_not_ignored_ones(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _write(
        tmp_path,
        {".gitignore": "build/\n", "a.py": "", "build/out.py": "", "gone.py": "", "new.py": ""},
    )
    _git(tmp_path, "add", ".gitignore", "a.py", "gone.py")
    (tmp_path / "gone.py").unlink()
    assert tree_paths(tmp_path) == [".gitignore", "a.py", "new.py"]


def test_outside_a_checkout_every_file_under_the_root(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "", "deep/b.md": "", ".hidden/c.txt": ""})
    assert tree_paths(tmp_path) == [".hidden/c.txt", "a.py", "deep/b.md"]


def test_files_match_the_lint_glob() -> None:
    files = tree({"a.py": "", "src/b.py": "", "src/deep/c.py": "", "src/d.md": ""})
    assert run_rule(todo, files) == []
    assert [each.path for each in files.files("src/*.py")] == ["src/b.py"]
    assert [each.path for each in files.files("src/**/*.py")] == ["src/b.py", "src/deep/c.py"]
    assert [each.path for each in files.files()] == [
        "a.py",
        "src/b.py",
        "src/d.md",
        "src/deep/c.py",
    ]


def test_a_file_reads_as_text_lines_json_yaml_and_python() -> None:
    files = tree({"a.json": '{"x": 1}', "b.yml": "x: [1, 2]\n", "c.py": "x = 1\n"})
    by_path = {each.path: each for each in files.files()}
    assert by_path["a.json"].json() == {"x": 1}
    assert by_path["b.yml"].yaml() == {"x": [1, 2]}
    assert by_path["c.py"].lines == ["x = 1"]
    assert by_path["c.py"].python().body


def test_a_new_untracked_file_is_linted_before_git_add(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path, {"new.py": "# TODO\n"})
    with compose_with("lint", provides={"acme": [TodoRules()]}):
        report = run(root=tmp_path)
    assert [(each.rule, each.target) for each in report.findings] == [("acme.todo", "new.py")]


def test_a_file_that_is_not_utf8_is_one_finding_and_the_rule_reads_the_rest(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"bad.py": b"\xff\xfe# TODO\n", "good.py": "# TODO\n"})
    with compose_with("lint", provides={"acme": [TodoRules()]}):
        report = run(root=tmp_path)
    assert sorted((each.rule, each.target) for each in report.findings) == [
        ("acme.todo", "good.py"),
        ("lint.unreadable-file", "bad.py"),
    ]
    assert not report.failures


def test_no_tree_is_built_when_no_rule_reads_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def listed(root: Path) -> list[str]:
        raise AssertionError("the tree was listed")

    monkeypatch.setattr("untaped_lint.application.engine.tree_paths", listed)
    with compose_with("lint"):
        report = run(root=tmp_path)
    assert report.warnings == [
        "nothing to lint here: run it in a plugin repository, a repository with "
        ".untaped/lint.yml, or pipe records with --stdin"
    ]
