"""CLI tests for ``workspace status --dirty --behind --check``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("isolate_config")


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _push_commit(upstream: Path, tmp_path: Path) -> None:
    seed = tmp_path / "_seed_behind"
    _git("clone", str(upstream), str(seed))
    _git("-C", str(seed), "config", "user.email", "t@t")
    _git("-C", str(seed), "config", "user.name", "t")
    (seed / "later.txt").write_text("later")
    _git("-C", str(seed), "add", ".")
    _git("-C", str(seed), "commit", "--no-gpg-sign", "-m", "later")
    _git("-C", str(seed), "push", "origin", "main")
    shutil.rmtree(seed)


@pytest.fixture
def workspace(tmp_path: Path, upstream: Path, isolated_cache: Path, isolate_config: Path) -> Path:
    """Three clones: ``clean``, ``dirty`` (untracked file), ``stale`` (behind)."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    for name in ("clean", "dirty", "stale"):
        shutil.copytree(upstream, tmp_path / f"{name}.git")
        runner.invoke(app, ["repos", "add", "smoke", f"file://{tmp_path / f'{name}.git'}"])
    assert runner.invoke(app, ["sync", "smoke"]).exit_code == 0
    (target / "dirty" / "wip.txt").write_text("wip")
    _push_commit(tmp_path / "stale.git", tmp_path)
    _git("-C", str(target / "stale"), "fetch", "-q", "origin")
    return target


def _status(*args: str) -> tuple[int, list[str]]:
    result = CliInvoker().invoke(
        app, ["status", "smoke", *args, "--format", "raw", "--columns", "repo"]
    )
    return result.exit_code, result.stdout.splitlines()


@pytest.mark.usefixtures("workspace")
@pytest.mark.parametrize(
    ("flags", "repos"),
    [
        ([], ["clean", "dirty", "stale"]),
        (["--dirty"], ["dirty"]),
        (["--behind"], ["stale"]),
        (["--dirty", "--behind"], ["dirty", "stale"]),
    ],
)
def test_status_filters_select_repos_needing_attention(flags: list[str], repos: list[str]) -> None:
    assert _status(*flags) == (0, repos)


@pytest.mark.usefixtures("workspace")
@pytest.mark.parametrize(
    ("flags", "repos"),
    [
        (["--check"], ["clean", "dirty", "stale"]),
        (["--check", "--dirty"], ["dirty"]),
        (["--check", "--behind"], ["stale"]),
    ],
)
def test_status_check_exits_three_when_a_repo_is_dirty_or_behind(
    flags: list[str], repos: list[str]
) -> None:
    assert _status(*flags) == (3, repos)


def test_status_check_exits_zero_when_every_repo_is_clean(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "smoke", "--path", str(tmp_path / "ws")])
    runner.invoke(app, ["repos", "add", "smoke", f"file://{upstream}"])
    runner.invoke(app, ["sync", "smoke"])

    result = runner.invoke(app, ["status", "smoke", "--check", "--dirty", "--behind"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No repos match" in result.stderr
