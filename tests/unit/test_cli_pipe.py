"""CLI tests for ``--format pipe`` kind tagging and ``path --stdin`` consumption."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_workspace import app

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_list_pipe_tags_workspace(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "main"])

    result = runner.invoke(app, ["list", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["untaped"] == "1"
    assert envelope["kind"] == "workspace.workspace"
    assert envelope["record"]["name"] == "prod"


def test_show_pipe_tags_repo(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "main"])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])

    result = runner.invoke(app, ["show", "--workspace", "prod", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip().splitlines()[0])
    assert envelope["kind"] == "workspace.repo"
    assert envelope["record"]["repo"] == "api"
    assert envelope["record"]["path"] == str(target.resolve())
    assert envelope["record"]["target_path"] == str((target / "api").resolve())


def test_show_pipe_empty_workspace_tags_summary_and_omits_target_path(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "main"])

    result = runner.invoke(app, ["show", "--workspace", "prod", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "workspace.summary"
    assert envelope["record"] == {
        "workspace": "prod",
        "path": str(target.resolve()),
        "default_branch": "main",
        "repo_count": 0,
        "repo": "",
        "url": "",
        "repo_branch": None,
        "target_branch": None,
    }
    assert "target_path" not in envelope["record"]


def test_path_stdin_consumes_list_pipe(tmp_path: Path) -> None:
    """`list --format pipe | path --stdin` extracts each record's name
    (id_field="name") and prints the resolved path."""
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "main"])

    list_out = runner.invoke(app, ["list", "--format", "pipe"]).stdout
    result = runner.invoke(app, ["path", "--stdin"], input=list_out)

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(target.resolve())


def test_path_stdin_bare_line_still_works(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "main"])

    result = runner.invoke(app, ["path", "--stdin"], input="prod\n")

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(target.resolve())


def test_sync_pipe_tags_sync_outcome(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])

    result = runner.invoke(app, ["sync", "--workspace", "smoke", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip().splitlines()[0])
    assert envelope["kind"] == "workspace.sync-outcome"
    assert set(envelope["record"]) == {"workspace", "repo", "action", "detail"}
    assert envelope["record"]["action"] == "clone"
    assert "Syncing repos" not in result.stdout
    assert "sync complete:" not in result.stdout


def test_sync_all_unavailable_manifest_pipe_row(
    tmp_path: Path,
    upstream: Path,
    isolate_config: Path,
    isolated_cache: Path,
) -> None:
    runner = CliInvoker()
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    ghost = tmp_path / "ghost"
    (alpha / "untaped.yml").write_text(
        f"name: alpha\nrepos:\n  - url: file://{upstream}\n    name: upstream\n",
        encoding="utf-8",
    )
    isolate_config.write_text(
        f"""
        workspace:
          workspaces:
            - name: alpha
              path: {alpha}
            - name: ghost
              path: {ghost}
        """,
        encoding="utf-8",
    )

    result = runner.invoke(app, ["sync", "--all", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelopes = [json.loads(line) for line in result.stdout.splitlines()]
    ghost = [e for e in envelopes if e["record"]["workspace"] == "ghost"]
    assert len(ghost) == 1
    assert ghost[0]["kind"] == "workspace.sync-outcome"
    assert ghost[0]["record"]["repo"] == ""
    assert ghost[0]["record"]["action"] == "unavailable"
    assert "workspace manifest unavailable: no manifest at" in ghost[0]["record"]["detail"]


def test_sync_prune_pipe_reports_unsafe_orphan_skip(tmp_path: Path, upstream: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    orphan = target / "scratch"
    subprocess.run(["git", "clone", str(upstream), str(orphan)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(orphan), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(orphan), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(orphan), "config", "commit.gpgsign", "false"], check=True)
    (orphan / "local.txt").write_text("local")
    subprocess.run(["git", "-C", str(orphan), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(orphan), "commit", "--no-gpg-sign", "-m", "local"],
        check=True,
        capture_output=True,
    )

    result = runner.invoke(app, ["sync", "--workspace", "smoke", "--prune", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "workspace.sync-outcome"
    assert envelope["record"] == {
        "workspace": "smoke",
        "repo": "scratch",
        "action": "skip",
        "detail": "unsafe local state: local commits not reachable from any remote-tracking ref",
    }
    assert orphan.is_dir()


def test_status_pipe_tags_status(tmp_path: Path, upstream: Path, isolated_cache: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(app, ["status", "--workspace", "smoke", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip().splitlines()[0])
    assert envelope["kind"] == "workspace.status"


def test_status_all_unavailable_manifest_pipe_row(
    tmp_path: Path,
    isolate_config: Path,
) -> None:
    runner = CliInvoker()
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    ghost = tmp_path / "ghost"
    (alpha / "untaped.yml").write_text(
        "name: alpha\nrepos:\n  - url: https://x/api.git\n    name: api\n",
        encoding="utf-8",
    )
    isolate_config.write_text(
        f"""
        workspace:
          workspaces:
            - name: alpha
              path: {alpha}
            - name: ghost
              path: {ghost}
        """,
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status", "--all", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelopes = [json.loads(line) for line in result.stdout.splitlines()]
    ghost_rows = [e for e in envelopes if e["record"]["workspace"] == "ghost"]
    assert len(ghost_rows) == 1
    assert ghost_rows[0]["kind"] == "workspace.status"
    assert ghost_rows[0]["record"]["repo"] == ""
    assert ghost_rows[0]["record"]["action"] == "unavailable"
    assert "workspace manifest unavailable: no manifest at" in ghost_rows[0]["record"]["detail"]


def test_foreach_pipe_tags_foreach_outcome(
    tmp_path: Path, upstream: Path, isolated_cache: Path
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])

    result = runner.invoke(
        app,
        ["foreach", "git rev-parse --abbrev-ref HEAD", "--workspace", "smoke", "--format", "pipe"],
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip().splitlines()[0])
    assert envelope["kind"] == "workspace.foreach-outcome"


def test_branch_apply_pipe_tags_branch_outcome(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "develop"])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])

    result = runner.invoke(app, ["branch", "apply", "--workspace", "prod", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip().splitlines()[0])
    assert envelope["kind"] == "workspace.branch-outcome"
