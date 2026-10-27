"""The `git.version` doctor row: the repo store's floor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped.sdk.testing import install
from untaped.sdk.testing.git import git_shim
from untaped_git.plugin import Git


def _version_row() -> tuple[int, dict[str, object]]:
    ran = install(Git).cli(["doctor", "--format", "json"])
    rows = {str(row["check"]): row for row in json.loads(ran.stdout)}
    return ran.exit, rows["git.version"]


def test_a_git_at_the_floor_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git_shim(tmp_path / "bin", monkeypatch, version="2.29.0")
    _, row = _version_row()
    assert (row["status"], row["detail"]) == ("pass", "git 2.29.0 (floor 2.29)")


def test_a_git_below_the_floor_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git_shim(tmp_path / "bin", monkeypatch, version="2.25.1")
    exit_code, row = _version_row()
    assert exit_code == 1
    assert row["status"] == "fail"
    assert "git 2.25.1 is older than 2.29" in str(row["detail"])


def test_no_git_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    _, row = _version_row()
    assert row["status"] == "warn"
