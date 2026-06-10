"""CLI tests for workspace branch commands."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from cli_fixtures import push_branch
from untaped.testing import CliInvoker

from untaped_workspace import app

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_branch_set_and_unset_default_branch(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])

    set_result = runner.invoke(app, ["branch", "set", "main", "--workspace", "prod"])
    unset_result = runner.invoke(app, ["branch", "unset", "--workspace", "prod"])

    assert set_result.exit_code == 0, set_result.output
    assert "set default branch for 'prod' to main" in set_result.output
    assert unset_result.exit_code == 0, unset_result.output
    assert "unset default branch for 'prod'" in unset_result.output
    raw = yaml.safe_load((target / "untaped.yml").read_text()) or {}
    assert "defaults" not in raw


def test_branch_set_and_unset_repo_branch(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])

    set_result = runner.invoke(
        app,
        ["branch", "set", "develop", "--repo", "api", "--workspace", "prod"],
    )
    manifest_after_set = yaml.safe_load((target / "untaped.yml").read_text())
    unset_result = runner.invoke(app, ["branch", "unset", "--repo", "api", "--workspace", "prod"])
    manifest_after_unset = yaml.safe_load((target / "untaped.yml").read_text())

    assert set_result.exit_code == 0, set_result.output
    assert "set branch for repo 'api' in 'prod' to develop" in set_result.output
    assert manifest_after_set["repos"][0]["branch"] == "develop"
    assert unset_result.exit_code == 0, unset_result.output
    assert "unset branch for repo 'api' in 'prod'" in unset_result.output
    assert "branch" not in manifest_after_unset["repos"][0]


def test_branch_commands_resolve_workspace_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    child = target / "src"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    child.mkdir()
    monkeypatch.chdir(child)

    result = runner.invoke(app, ["branch", "set", "main"])

    assert result.exit_code == 0, result.output
    assert "set default branch for 'prod' to main" in result.output
    raw = yaml.safe_load((target / "untaped.yml").read_text())
    assert raw["defaults"]["branch"] == "main"


def test_branch_set_unknown_repo_errors(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])

    result = runner.invoke(app, ["branch", "set", "main", "--repo", "ghost", "--workspace", "prod"])

    assert result.exit_code == 1
    assert "repo 'ghost' not declared in workspace 'prod'" in result.output


def test_branch_apply_json_skips_missing_clone(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "develop"])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])

    result = runner.invoke(app, ["branch", "apply", "--workspace", "prod", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "repo": "api",
            "workspace": "prod",
            "target_branch": "develop",
            "action": "skip",
            "detail": "not cloned",
        }
    ]


def test_branch_apply_filters_by_multiple_repo_names(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "develop"])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])
    runner.invoke(app, ["add", "https://x/ui.git", "--repo-name", "ui", "--workspace", "prod"])
    runner.invoke(app, ["add", "https://x/docs.git", "--repo-name", "docs", "--workspace", "prod"])

    result = runner.invoke(
        app,
        [
            "branch",
            "apply",
            "--repo",
            "api",
            "--repo",
            "ui",
            "--workspace",
            "prod",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [row["repo"] for row in rows] == ["api", "ui"]


def test_branch_apply_raw_defaults_to_repo_names(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "develop"])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])
    runner.invoke(app, ["add", "https://x/ui.git", "--repo-name", "ui", "--workspace", "prod"])

    result = runner.invoke(app, ["branch", "apply", "--workspace", "prod", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["api", "ui"]


def test_branch_apply_honors_global_ui_collection_view_for_table_output(
    isolate_config: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "ws"
    target.mkdir()
    (target / "untaped.yml").write_text(
        "name: prod\n"
        "defaults:\n"
        "  branch: develop\n"
        "repos:\n"
        "  - url: https://x/api.git\n"
        "    name: api\n"
    )
    isolate_config.write_text(
        f"""
        ui:
          collection_view: list
        workspace:
          workspaces:
            - name: prod
              path: {target}
        """
    )

    result = CliInvoker().invoke(
        app,
        [
            "branch",
            "apply",
            "--workspace",
            "prod",
            "--format",
            "table",
            "--columns",
            "workspace",
            "--columns",
            "repo",
            "--columns",
            "target_branch",
            "--columns",
            "action",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "workspace: prod" in result.stdout
    assert "repo: api" in result.stdout
    assert "target_branch: develop" in result.stdout
    assert "action: skip" in result.stdout
    assert "╭" not in result.stdout
    assert "┌" not in result.stdout


def test_branch_set_apply_writes_manifest_and_applies(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])

    result = runner.invoke(
        app,
        ["branch", "set", "develop", "--apply", "--workspace", "prod", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert "set default branch for 'prod' to develop" in result.output
    assert json.loads(result.stdout) == [
        {
            "repo": "api",
            "workspace": "prod",
            "target_branch": "develop",
            "action": "skip",
            "detail": "not cloned",
        }
    ]
    raw = yaml.safe_load((target / "untaped.yml").read_text())
    assert raw["defaults"]["branch"] == "develop"


def test_branch_set_apply_creates_tracking_branch_for_remote_target(
    tmp_path: Path,
    upstream: Path,
    isolated_cache: Path,
) -> None:
    push_branch(upstream, tmp_path, branch="develop")
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])
    repo = target / "upstream"
    subprocess.run(["git", "-C", str(repo), "config", "checkout.guess", "false"], check=True)

    result = runner.invoke(
        app,
        ["branch", "set", "develop", "--workspace", "smoke", "--apply", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "repo": "upstream",
            "workspace": "smoke",
            "target_branch": "develop",
            "action": "checkout",
            "detail": "from main",
        }
    ]
    head = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracking_remote = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "branch.develop.remote"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "develop"
    assert tracking_remote == "origin"


def test_branch_apply_creates_tracking_branch_for_single_branch_clone(
    tmp_path: Path,
    upstream: Path,
    isolated_cache: Path,
) -> None:
    push_branch(upstream, tmp_path, branch="develop")
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target), "--branch", "develop"])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    repo = target / "upstream"
    subprocess.run(
        [
            "git",
            "clone",
            "--single-branch",
            "--branch",
            "main",
            str(upstream),
            str(repo),
        ],
        check=True,
        capture_output=True,
    )

    result = runner.invoke(app, ["branch", "apply", "--workspace", "smoke", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "repo": "upstream",
            "workspace": "smoke",
            "target_branch": "develop",
            "action": "checkout",
            "detail": "from main",
        }
    ]
    head = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracking_remote = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "branch.develop.remote"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "develop"
    assert tracking_remote == "origin"


def test_branch_apply_creates_local_branch_when_remote_target_is_missing(
    tmp_path: Path,
    upstream: Path,
    isolated_cache: Path,
) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "smoke", "--path", str(target)])
    runner.invoke(app, ["add", f"file://{upstream}", "--workspace", "smoke"])
    runner.invoke(app, ["sync", "--workspace", "smoke"])
    runner.invoke(app, ["branch", "set", "ticket-123", "--workspace", "smoke"])
    repo = target / "upstream"

    result = runner.invoke(app, ["branch", "apply", "--workspace", "smoke", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "repo": "upstream",
            "workspace": "smoke",
            "target_branch": "ticket-123",
            "action": "checkout",
            "detail": "from main",
        }
    ]
    head = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracking_remote = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "branch.ticket-123.remote"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert head == "ticket-123"
    assert tracking_remote.returncode != 0
