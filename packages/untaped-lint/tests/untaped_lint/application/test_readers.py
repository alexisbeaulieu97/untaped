"""``readers:``: a plugin turns the files its globs match into records, and only there."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from lint_fakes import Acme, Widget
from untaped.sdk import ConfigError, Record
from untaped.sdk.contracts import Provider
from untaped.sdk.testing import install
from untaped_lint.api import Tree, TreeReader
from untaped_lint.plugin import LintPlugin
from untaped_lint.testing import run

pytestmark = pytest.mark.usefixtures("fresh_composition")


class WidgetFiles(TreeReader, Provider):
    """Reads each ``*.widget`` file it is handed into a ``Widget``."""

    #: The paths of each tree it was handed; core makes the reader, so the class keeps them.
    handed: ClassVar[list[tuple[str, ...]]] = []

    def read(self, tree: Tree) -> list[Record]:
        type(self).handed.append(tree.paths)
        return list(self._subjects(tree))

    def _subjects(self, tree: Tree) -> Iterator[Record]:
        for file in tree.files():
            yield Widget(name=file.text.strip())


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
    WidgetFiles.handed.clear()
    root = _repo(tmp_path, "  widgets: ['specs/**/*.widget']\n")
    with install(LintPlugin, providers={"widgets": [WidgetFiles], "acme": [Acme]}).running():
        report = run(root=root)
    assert WidgetFiles.handed == [("specs/a.widget", "specs/b.widget")]
    assert sorted((each.rule, each.target) for each in report.findings) == [
        ("acme.tall", "a"),
        ("acme.tall", "b"),
        ("acme.wide", "a"),
        ("acme.wide", "b"),
    ]


def test_a_reader_runs_only_where_the_file_names_it(tmp_path: Path) -> None:
    WidgetFiles.handed.clear()
    (tmp_path / ".git").mkdir()
    with install(LintPlugin, providers={"widgets": [WidgetFiles], "acme": [Acme]}).running():
        run(root=tmp_path)
    assert WidgetFiles.handed == []


def test_a_readers_key_that_reads_nothing_is_a_config_error(tmp_path: Path) -> None:
    root = _repo(tmp_path, "  ghost: ['specs/**']\n")
    with install(LintPlugin).running(), pytest.raises(ConfigError, match=r"untaped\[ghost\]"):
        run(root=root)
