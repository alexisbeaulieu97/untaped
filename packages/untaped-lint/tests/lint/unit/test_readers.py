"""``readers:``: a plugin turns the files its globs match into subjects, and only there."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lint.fakes import Acme, Widget
from untaped.sdk import ConfigError
from untaped.sdk.testing import compose_with
from untaped_lint.api import Subject, Tree, TreeReader
from untaped_lint.testing import run, subject

pytestmark = pytest.mark.usefixtures("fresh_composition")


class WidgetFiles(TreeReader):
    """Reads each ``*.widget`` file it is handed into a ``linttest.widget`` subject."""

    def __init__(self) -> None:
        self.handed: list[tuple[str, ...]] = []

    def read(self, tree: Tree) -> list[Subject]:
        self.handed.append(tree.paths)
        return list(self._subjects(tree))

    def _subjects(self, tree: Tree) -> Iterator[Subject]:
        for file in tree.files():
            yield subject(Widget(name=file.text.strip()))


def _repo(root: Path, readers: str) -> Path:
    (root / ".git").mkdir()
    (root / ".untaped").mkdir()
    (root / ".untaped" / "lint.yml").write_text(
        f"version: 1\nname: house\nreaders:\n{readers}", encoding="utf-8"
    )
    for name in ("specs/a.widget", "specs/b.widget", "roles/x.widget"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(Path(name).stem, encoding="utf-8")
    return root


def test_a_reader_reads_only_the_files_its_globs_match(tmp_path: Path) -> None:
    reader = WidgetFiles()
    root = _repo(tmp_path, "  widgets: ['specs/**/*.widget']\n")
    with compose_with("lint", provides={"widgets": [reader], "acme": [Acme()]}):
        report = run(root=root)
    assert reader.handed == [("specs/a.widget", "specs/b.widget")]
    assert sorted((each.rule, each.target) for each in report.findings) == [
        ("acme.tall", "a"),
        ("acme.tall", "b"),
        ("acme.wide", "a"),
        ("acme.wide", "b"),
    ]


def test_a_reader_runs_only_where_the_file_names_it(tmp_path: Path) -> None:
    reader = WidgetFiles()
    (tmp_path / ".git").mkdir()
    with compose_with("lint", provides={"widgets": [reader], "acme": [Acme()]}):
        run(root=tmp_path)
    assert reader.handed == []


def test_a_readers_key_that_reads_nothing_is_a_config_error(tmp_path: Path) -> None:
    root = _repo(tmp_path, "  ghost: ['specs/**']\n")
    with compose_with("lint"), pytest.raises(ConfigError, match=r"untaped\[ghost\]"):
        run(root=root)
