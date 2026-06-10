"""CLI tests for workspace display and UX commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_workspace import app

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_show_workspace_json_details(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "main"])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])
    runner.invoke(
        app,
        [
            "add",
            "https://x/ui.git",
            "--repo-name",
            "ui",
            "--branch",
            "develop",
            "--workspace",
            "prod",
        ],
    )

    result = runner.invoke(app, ["show", "--workspace", "prod", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "workspace": "prod",
            "path": str(target.resolve()),
            "default_branch": "main",
            "repo_count": 2,
            "repo": "api",
            "url": "https://x/api.git",
            "repo_branch": None,
            "target_branch": "main",
        },
        {
            "workspace": "prod",
            "path": str(target.resolve()),
            "default_branch": "main",
            "repo_count": 2,
            "repo": "ui",
            "url": "https://x/ui.git",
            "repo_branch": "develop",
            "target_branch": "develop",
        },
    ]


def test_show_workspace_by_path_json_details(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "main"])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])

    result = runner.invoke(app, ["show", "--path", str(target), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "workspace": "prod",
            "path": str(target.resolve()),
            "default_branch": "main",
            "repo_count": 1,
            "repo": "api",
            "url": "https://x/api.git",
            "repo_branch": None,
            "target_branch": "main",
        }
    ]


def test_show_rejects_workspace_and_path_together(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])

    result = runner.invoke(app, ["show", "--workspace", "prod", "--path", str(target)])

    assert result.exit_code != 0
    assert "--workspace and --path are mutually exclusive" in result.output


def test_show_empty_workspace_outputs_summary_row(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "empty"
    runner.invoke(app, ["init", "empty", "--path", str(target)])

    result = runner.invoke(app, ["show", "--workspace", "empty", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "workspace": "empty",
            "path": str(target.resolve()),
            "default_branch": None,
            "repo_count": 0,
            "repo": "",
            "url": "",
            "repo_branch": None,
            "target_branch": None,
        }
    ]


def test_show_raw_columns_emit_repo_names(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    runner.invoke(app, ["add", "https://x/api.git", "--repo-name", "api", "--workspace", "prod"])

    result = runner.invoke(
        app, ["show", "--workspace", "prod", "--format", "raw", "--columns", "repo"]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["api"]


def test_show_accepts_workspace_short_option(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])

    result = runner.invoke(app, ["show", "-w", "prod", "--format", "json"])

    assert result.exit_code == 0, result.output


def test_path_prints_workspace_path(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws-prod"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    out = runner.invoke(app, ["path", "prod"])
    assert out.exit_code == 0
    assert out.stdout.strip() == str(target.resolve())


def test_path_unknown_workspace_errors() -> None:
    result = CliInvoker().invoke(app, ["path", "ghost"])
    assert result.exit_code == 1


def test_shell_init_zsh() -> None:
    result = CliInvoker().invoke(app, ["shell-init", "zsh"])
    assert result.exit_code == 0
    assert "uwcd()" in result.stdout


def test_shell_init_fish() -> None:
    result = CliInvoker().invoke(app, ["shell-init", "fish"])
    assert result.exit_code == 0
    assert "function uwcd" in result.stdout


def test_shell_init_unknown() -> None:
    result = CliInvoker().invoke(app, ["shell-init", "powershell"])
    assert result.exit_code == 1


def test_edit_from_cwd_opens_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []

    def _runner(argv: list[str]) -> int:
        captured.append(argv)
        return 0

    monkeypatch.setattr("untaped_workspace.cli.ux_commands.editor_runner", _runner)
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    nested = target / "api"
    nested.mkdir()
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["edit", "--editor", "code --reuse-window"])

    assert result.exit_code == 0, result.output
    assert captured == [["code", "--reuse-window", str(target.resolve())]]


def test_edit_path_opens_unregistered_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "untaped_workspace.cli.ux_commands.editor_runner",
        lambda argv: captured.append(argv) or 0,
    )
    target = tmp_path / "ws"
    target.mkdir()
    (target / "untaped.yml").write_text("name: prod\nrepos: []\n")

    result = CliInvoker().invoke(app, ["edit", "--path", str(target), "--editor", "code"])

    assert result.exit_code == 0, result.output
    assert captured == [["code", str(target.resolve())]]


def test_edit_workspace_opens_registered_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "untaped_workspace.cli.ux_commands.editor_runner",
        lambda argv: captured.append(argv) or 0,
    )
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])

    result = runner.invoke(app, ["edit", "--workspace", "prod", "--editor", "code"])

    assert result.exit_code == 0
    assert captured == [["code", str(target.resolve())]]


def test_edit_missing_context_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliInvoker().invoke(app, ["edit", "--editor", "true"])

    assert result.exit_code == 1
    assert "not inside a workspace" in result.output


def test_edit_editor_not_found_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing(_argv: list[str]) -> int:
        raise FileNotFoundError("no such file")

    monkeypatch.setattr("untaped_workspace.cli.ux_commands.editor_runner", _missing)
    target = tmp_path / "ws"
    target.mkdir()
    (target / "untaped.yml").write_text("name: prod\nrepos: []\n")

    result = CliInvoker().invoke(app, ["edit", "--path", str(target), "--editor", "code"])

    assert result.exit_code == 1
    assert "editor not found: code" in result.output


def test_path_accepts_multiple_positional_names(tmp_path: Path) -> None:
    """``workspace path a b`` echoes one path per name in input order."""
    runner = CliInvoker()
    target_a = tmp_path / "ws-a"
    target_b = tmp_path / "ws-b"
    runner.invoke(app, ["init", "alpha", "--path", str(target_a)])
    runner.invoke(app, ["init", "beta", "--path", str(target_b)])
    result = runner.invoke(app, ["path", "alpha", "beta"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines == [str(target_a.resolve()), str(target_b.resolve())]


def test_path_reads_names_from_stdin(tmp_path: Path) -> None:
    """``workspace list --format raw | workspace path --stdin`` emits
    one absolute path per registered workspace."""
    runner = CliInvoker()
    target_a = tmp_path / "ws-a"
    target_b = tmp_path / "ws-b"
    runner.invoke(app, ["init", "alpha", "--path", str(target_a)])
    runner.invoke(app, ["init", "beta", "--path", str(target_b)])
    result = runner.invoke(app, ["path", "--stdin"], input="alpha\nbeta\n")
    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines == [str(target_a.resolve()), str(target_b.resolve())]


def test_path_continues_when_one_name_missing(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "alpha", "--path", str(target)])
    result = runner.invoke(app, ["path", "ghost", "alpha"])
    assert result.exit_code != 0
    # Known workspace's path reaches stdout; per-id error stays on
    # stderr so ``cd "$(workspace path …)"`` doesn't ingest the row.
    assert result.stdout.strip().splitlines() == [str(target.resolve())]
    assert "error: ghost" in (result.stderr or "")
    assert "error:" not in result.stdout


def test_path_rejects_mixed_positional_and_stdin(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "alpha", "--path", str(target)])
    result = runner.invoke(app, ["path", "alpha", "--stdin"], input="alpha\n")
    assert result.exit_code != 0
    assert "stdin" in (result.output + (result.stderr or "")).lower()
