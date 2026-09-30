"""Default ``--format table`` columns of the workspace commands.

``--columns ?`` marks a command's default table columns with ``*``; every
other format keeps the whole record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("isolate_config")


def _defaults(stderr: str) -> set[str]:
    """The columns ``--columns ?`` marks as shown by default."""
    return {line.split()[0] for line in stderr.splitlines() if line.endswith(" *")}


def _header(stdout: str) -> list[str]:
    """The column names of a rendered table."""
    line = next(line for line in stdout.splitlines() if "repo" in line)
    return [cell.strip() for cell in line.strip("│ ").split("│") if cell.strip()]


@pytest.fixture
def synced(tmp_path: Path, upstream: Path, isolated_cache: Path, isolate_config: Path) -> Path:
    """Workspace ``smoke`` with one clean clone of ``upstream``."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["repos", "add", "smoke", f"file://{upstream}"])
    synced = runner.invoke(app, ["sync", "smoke"])
    assert synced.exit_code == 0, synced.output
    return target


@pytest.mark.usefixtures("synced")
def test_status_table_defaults_leave_out_constant_and_derivable_fields() -> None:
    single = CliInvoker().invoke(app, ["status", "smoke", "--columns", "?"])
    every = CliInvoker().invoke(app, ["status", "--all", "--columns", "?"])

    columns = ["detail", "cloned", "branch", "upstream", "ahead", "behind", "modified", "untracked"]
    assert _defaults(single.stderr) == {"repo", *columns}
    assert _defaults(every.stderr) == {"workspace", "repo", *columns}


@pytest.mark.usefixtures("synced")
def test_status_record_carries_the_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    status = CliInvoker().invoke(app, ["status", "smoke", "--format", "json"])
    assert status.exit_code == 0, status.output
    rows = json.loads(status.stdout)
    table = CliInvoker().invoke(app, ["status", "smoke"])

    assert rows[0]["upstream"] == "origin/main"
    assert list(rows[0])[:3] == ["workspace", "repo", "action"]
    assert _header(table.stdout) == [
        "repo",
        "cloned",
        "branch",
        "upstream",
        "ahead",
        "behind",
        "modified",
        "untracked",
    ]


@pytest.mark.usefixtures("synced")
def test_sync_table_defaults_add_the_workspace_only_across_workspaces() -> None:
    single = CliInvoker().invoke(app, ["sync", "smoke", "--columns", "?"])
    every = CliInvoker().invoke(app, ["sync", "--all", "--columns", "?"])

    assert _defaults(single.stderr) == {"repo", "action", "detail"}
    assert _defaults(every.stderr) == {"workspace", "repo", "action", "detail"}


def test_branch_apply_table_shows_the_target_branch_only_when_it_varies(tmp_path: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "prod", "--path", str(tmp_path / "ws"), "--branch", "develop"])
    runner.invoke(app, ["repos", "add", "prod", "https://x/api.git", "--repo-name", "api"])
    runner.invoke(app, ["repos", "add", "prod", "https://x/ui.git", "--repo-name", "ui"])

    same = runner.invoke(app, ["branch", "apply", "prod", "--columns", "?"])
    runner.invoke(app, ["branch", "set", "prod", "release", "--repo", "ui"])
    varies = runner.invoke(app, ["branch", "apply", "prod", "--columns", "?"])

    assert _defaults(same.stderr) == {"repo", "action", "detail"}
    assert _defaults(varies.stderr) == {"repo", "target_branch", "action", "detail"}


def test_repos_list_table_defaults_and_repo_first(tmp_path: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "prod", "--path", str(tmp_path / "ws")])
    runner.invoke(app, ["repos", "add", "prod", "https://x/api.git", "https://x/ui.git"])

    listed = runner.invoke(app, ["repos", "list", "prod", "--columns", "?"])
    raw = runner.invoke(app, ["repos", "list", "prod", "--format", "raw"])

    assert _defaults(listed.stderr) == {"repo", "url", "target_branch"}
    assert [line.split("\t")[0] for line in raw.stdout.splitlines()] == ["api", "ui"]


def test_repos_add_table_defaults(tmp_path: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "prod", "--path", str(tmp_path / "ws")])

    added = runner.invoke(app, ["repos", "add", "prod", "https://x/api.git", "--columns", "?"])

    assert added.exit_code == 0, added.output
    assert _defaults(added.stderr) == {"repo", "url", "branch", "action"}
