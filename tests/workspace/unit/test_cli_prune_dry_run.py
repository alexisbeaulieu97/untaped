"""CLI tests for ``--dry-run`` on every workspace prune.

``forget --prune --dry-run`` and ``sync --prune --dry-run`` preview what
would be deleted, run the same safety checks, and write nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("isolate_config")


def _synced(runner: CliInvoker, tmp_path: Path, upstream: Path) -> Path:
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["repos", "add", "smoke", f"file://{upstream}"])
    result = runner.invoke(app, ["sync", "smoke"])
    assert result.exit_code == 0, result.output
    return target.resolve()


def _registered(runner: CliInvoker) -> list[str]:
    result = runner.invoke(app, ["list", "--format", "raw", "--columns", "name"])
    return result.stdout.splitlines()


def test_forget_prune_dry_run_previews_and_keeps_everything(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = _synced(runner, tmp_path, upstream)

    result = runner.invoke(app, ["forget", "smoke", "--prune", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"name": "smoke", "action": "planned", "target_path": str(target)}
    ]
    assert str(target / "upstream") in result.stderr
    assert str(target / "untaped.yml") in result.stderr
    assert (target / "upstream" / ".git").is_dir()
    assert (target / "untaped.yml").is_file()
    assert _registered(runner) == ["smoke"]


def test_forget_prune_dry_run_reports_unsafe_state_like_a_real_prune(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = _synced(runner, tmp_path, upstream)
    (target / "upstream" / "wip.txt").write_text("wip")

    result = runner.invoke(app, ["forget", "smoke", "--prune", "--dry-run"])

    assert result.exit_code == 1
    assert "refusing to prune 'smoke'" in result.stderr
    assert _registered(runner) == ["smoke"]


def test_forget_dry_run_without_prune_keeps_the_registry_entry(tmp_path: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "smoke", "--path", str(tmp_path / "ws")])

    result = runner.invoke(app, ["forget", "smoke", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "planned"
    assert _registered(runner) == ["smoke"]


def test_sync_prune_dry_run_plans_orphans_without_syncing_or_deleting(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = _synced(runner, tmp_path, upstream)
    runner.invoke(app, ["repos", "remove", "smoke", "upstream"])
    runner.invoke(app, ["repos", "add", "smoke", f"file://{upstream}", "--repo-name", "fresh"])

    result = runner.invoke(app, ["sync", "smoke", "--prune", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [(r["repo"], r["action"]) for r in rows] == [("upstream", "planned")]
    assert (target / "upstream" / ".git").is_dir()
    assert not (target / "fresh").exists()


def test_sync_prune_dry_run_keeps_unsafe_orphans_as_skipped_rows(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = _synced(runner, tmp_path, upstream)
    runner.invoke(app, ["repos", "remove", "smoke", "upstream"])
    (target / "upstream" / "wip.txt").write_text("wip")

    result = runner.invoke(app, ["sync", "smoke", "--prune", "--dry-run", "--format", "json"])

    assert result.exit_code == 0, result.output
    (row,) = json.loads(result.stdout)
    assert row["action"] == "skipped"
    assert (target / "upstream").is_dir()


def test_sync_dry_run_requires_prune() -> None:
    result = CliInvoker().invoke(app, ["sync", "--all", "--dry-run"])

    assert result.exit_code == 2
    assert "--dry-run requires --prune" in result.stderr
