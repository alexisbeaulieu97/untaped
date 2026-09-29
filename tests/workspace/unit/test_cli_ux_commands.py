"""CLI tests for workspace display and UX commands."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_repos_list_json_details(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "main"])
    runner.invoke(app, ["repos", "add", "prod", "https://x/api.git", "--repo-name", "api"])
    runner.invoke(
        app,
        ["repos", "add", "prod", "https://x/ui.git", "--repo-name", "ui", "--branch", "develop"],
    )

    result = runner.invoke(app, ["repos", "list", "prod", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "workspace": "prod",
            "path": str(target.resolve()),
            "target_path": str((target / "api").resolve()),
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
            "target_path": str((target / "ui").resolve()),
            "default_branch": "main",
            "repo_count": 2,
            "repo": "ui",
            "url": "https://x/ui.git",
            "repo_branch": "develop",
            "target_branch": "develop",
        },
    ]


def test_repos_list_by_path_json_details(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target), "--branch", "main"])
    runner.invoke(app, ["repos", "add", "prod", "https://x/api.git", "--repo-name", "api"])

    result = runner.invoke(app, ["repos", "list", str(target), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "workspace": "prod",
            "path": str(target.resolve()),
            "target_path": str((target / "api").resolve()),
            "default_branch": "main",
            "repo_count": 1,
            "repo": "api",
            "url": "https://x/api.git",
            "repo_branch": None,
            "target_branch": "main",
        }
    ]


def test_repos_list_empty_workspace_outputs_summary_row(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "empty"
    runner.invoke(app, ["init", "empty", "--path", str(target)])

    result = runner.invoke(app, ["repos", "list", "empty", "--format", "json"])

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


def test_repos_list_raw_columns_emit_repo_names(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    runner.invoke(app, ["repos", "add", "prod", "https://x/api.git", "--repo-name", "api"])

    result = runner.invoke(app, ["repos", "list", "prod", "--format", "raw", "--columns", "repo"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["api"]


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


def _recording_editor(tmp_path: Path) -> tuple[str, Path]:
    """An ``--editor`` command that records its argv (after the script) as JSON."""
    script = tmp_path / "record_editor.py"
    record = tmp_path / "editor-argv.json"
    script.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    return shlex.join([sys.executable, str(script)]), record


def test_edit_from_cwd_opens_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor, record = _recording_editor(tmp_path)
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])
    nested = target / "api"
    nested.mkdir()
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["edit", "--editor", f"{editor} --reuse-window"])

    assert result.exit_code == 0, result.output
    assert json.loads(record.read_text()) == ["--reuse-window", str(target.resolve())]


def test_edit_path_opens_unregistered_workspace(tmp_path: Path) -> None:
    editor, record = _recording_editor(tmp_path)
    target = tmp_path / "ws"
    target.mkdir()
    (target / "untaped.yml").write_text("name: prod\nrepos: []\n")

    result = CliInvoker().invoke(app, ["edit", str(target), "--editor", editor])

    assert result.exit_code == 0, result.output
    assert json.loads(record.read_text()) == [str(target.resolve())]


def test_edit_workspace_uses_visual_then_editor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor, record = _recording_editor(tmp_path)
    monkeypatch.setenv("VISUAL", editor)
    monkeypatch.setenv("EDITOR", "does-not-exist")
    runner = CliInvoker()
    target = tmp_path / "ws"
    runner.invoke(app, ["init", "prod", "--path", str(target)])

    result = runner.invoke(app, ["edit", "prod"])

    assert result.exit_code == 0, result.output
    assert json.loads(record.read_text()) == [str(target.resolve())]


def test_edit_without_editor_fails_with_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    target = tmp_path / "ws"
    target.mkdir()
    (target / "untaped.yml").write_text("name: prod\nrepos: []\n")

    result = CliInvoker().invoke(app, ["edit", str(target)])

    assert result.exit_code == 4  # the environment needs fixing
    assert "set $VISUAL or $EDITOR" in result.stderr


def test_edit_missing_context_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliInvoker().invoke(app, ["edit", "--editor", "true"])

    assert result.exit_code == 1
    assert "not inside a workspace" in result.output


def test_edit_editor_not_found_errors(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    target.mkdir()
    (target / "untaped.yml").write_text("name: prod\nrepos: []\n")

    result = CliInvoker().invoke(app, ["edit", str(target), "--editor", "definitely-missing-bin"])

    assert result.exit_code == 4  # the environment needs fixing
    assert "editor not found: definitely-missing-bin" in result.output


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


def test_list_empty_guides_with_stderr_hint() -> None:
    result = CliInvoker().invoke(app, ["list"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No workspaces registered" in result.stderr


def test_list_empty_json_stays_pipe_clean() -> None:
    result = CliInvoker().invoke(app, ["list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "[]"
    assert "No workspaces registered" not in result.stderr
