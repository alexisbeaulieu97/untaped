"""``lint_config``: the profile's ignores and the repository's lint file, built in one place."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.testing import compose_with
from untaped_lint.api import lint_config

pytestmark = pytest.mark.usefixtures("fresh_composition")


def _repo(root: Path) -> Path:
    (root / ".git").mkdir()
    (root / ".untaped").mkdir()
    (root / ".untaped" / "lint.yml").write_text(
        "version: 1\nname: house\nignore: ['acme.wide:legacy-*']\n", encoding="utf-8"
    )
    (root / "specs").mkdir()
    return root


def test_it_reads_the_profile_and_the_repo_file_found_walking_up(
    tmp_path: Path, _isolated_config: Path
) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    lint:\n      ignore: [acme.tall]\n", encoding="utf-8"
    )
    root = _repo(tmp_path)
    with compose_with("lint"):
        config = lint_config(root / "specs")
    assert config.ignore == ("acme.tall",)
    assert config.repo is not None
    assert config.repo.ignore == ("acme.wide:legacy-*",)
    assert config.tree_root == root.resolve()


def test_without_the_tree_the_repo_file_still_applies(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with compose_with("lint"):
        config = lint_config(root / "specs", tree=False)
    assert config.tree_root is None
    assert config.repo is not None
    assert config.repo.name == "house"


def test_outside_a_repository_there_is_no_repo_file(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with compose_with("lint"):
        config = lint_config(tmp_path)
    assert config.repo is None
    assert config.tree_root == tmp_path.resolve()
