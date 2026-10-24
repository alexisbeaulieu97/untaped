"""A `git` planted in the current folder is never the one the git plugin runs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from untaped_git.infrastructure.version import git_version

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX PATH lookup")


@pytest.fixture
def planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository folder holding a `git`, current, with `.` first on PATH."""
    repo = tmp_path / "repo"
    repo.mkdir()
    program = repo / "git"
    program.write_text(f"#!{sys.executable}\nprint('git version 9.9.9')\n")
    program.chmod(0o755)
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", f".{os.pathsep}{os.environ['PATH']}")
    return repo


def test_the_version_probe_skips_a_planted_git(planted: Path) -> None:
    assert git_version() != (9, 9, 9)
