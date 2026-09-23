"""CLI tests for the workspace mutation outcome rows, stdin kinds and quiet mode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.capabilities.workspace import SPEC
from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("isolate_config")


def _pipe(kind: str, **record: object) -> str:
    return json.dumps({"untaped": "1", "kind": kind, "record": record}) + "\n"


def _init(runner: CliInvoker, tmp_path: Path, name: str = "prod") -> Path:
    target = tmp_path / name
    result = runner.invoke(app, ["init", name, "--path", str(target)])
    assert result.exit_code == 0, result.output
    return target.resolve()


def test_init_emits_init_outcome(tmp_path: Path) -> None:
    result = CliInvoker().invoke(
        app, ["init", "prod", "--path", str(tmp_path / "ws"), "--format", "pipe"]
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["kind"] == "workspace.init_outcome"
    assert envelope["record"] == {
        "name": "prod",
        "action": "created",
        "target_path": str((tmp_path / "ws").resolve()),
    }
    assert "initialized workspace 'prod'" in result.stderr


def test_add_emits_add_outcome_rows(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = _init(runner, tmp_path)

    result = runner.invoke(
        app,
        ["add", "https://x/api.git", "https://x/ui.git", "-w", "prod", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "workspace": "prod",
            "repo": "api",
            "url": "https://x/api.git",
            "branch": None,
            "action": "added",
            "target_path": str(target / "api"),
        },
        {
            "workspace": "prod",
            "repo": "ui",
            "url": "https://x/ui.git",
            "branch": None,
            "action": "added",
            "target_path": str(target / "ui"),
        },
    ]


def test_add_stdin_reads_github_repo_clone_urls(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    result = runner.invoke(
        app,
        ["add", "--stdin", "-w", "prod", "--format", "raw", "--columns", "url"],
        input=_pipe("github.repo", full_name="acme/api", clone_url="https://x/api.git"),
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["https://x/api.git"]


def test_add_stdin_rejects_foreign_kind(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    result = runner.invoke(
        app,
        ["add", "--stdin", "-w", "prod"],
        input=_pipe("awx.host", id=1, name="h1"),
    )

    assert result.exit_code == 2, result.output


def test_remove_dry_run_plans_without_mutating(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = _init(runner, tmp_path)
    runner.invoke(app, ["add", "https://x/api.git", "-w", "prod"])

    result = runner.invoke(
        app, ["remove", "api", "-w", "prod", "--prune", "--dry-run", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"workspace": "prod", "repo": "api", "action": "planned", "pruned": True}
    ]
    assert "api.git" in (target / "untaped.yml").read_text()


def test_remove_emits_remove_outcome_and_reads_repo_records(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)
    runner.invoke(app, ["add", "https://x/api.git", "-w", "prod"])

    result = runner.invoke(
        app,
        ["remove", "--stdin", "-w", "prod", "--format", "pipe"],
        input=_pipe("workspace.repo", workspace="prod", repo="api", url="https://x/api.git"),
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["kind"] == "workspace.remove_outcome"
    assert envelope["record"] == {
        "workspace": "prod",
        "repo": "api",
        "action": "removed",
        "pruned": False,
    }


def test_remove_stdin_rejects_foreign_kind(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    result = runner.invoke(
        app,
        ["remove", "--stdin", "-w", "prod"],
        input=_pipe("workspace.workspace", name="prod", path=str(tmp_path)),
    )

    assert result.exit_code == 2, result.output


def test_path_stdin_rejects_foreign_kind(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    result = runner.invoke(
        app, ["path", "--stdin"], input=_pipe("workspace.repo", workspace="prod", repo="x")
    )

    assert result.exit_code == 2, result.output


def test_forget_emits_forget_outcome(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = _init(runner, tmp_path)

    result = runner.invoke(app, ["forget", "prod", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"name": "prod", "action": "forgotten", "target_path": str(target)}
    ]


def test_branch_unset_emits_outcome(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)
    runner.invoke(app, ["branch", "set", "main", "-w", "prod"])

    result = runner.invoke(app, ["branch", "unset", "-w", "prod", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"workspace": "prod", "repo": None, "branch": None, "action": "updated"}
    ]


def test_edit_nonzero_editor_exit_is_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "untaped.capabilities.workspace.cli.ux_commands.editor_runner", lambda argv: 7
    )
    runner = CliInvoker()
    _init(runner, tmp_path)

    result = runner.invoke(app, ["edit", "-w", "prod", "--editor", "code"])

    assert result.exit_code == 1, result.output
    assert "error: editor exited with status 7" in result.stderr


def test_quiet_mutes_success_lines(tmp_path: Path) -> None:
    runner = CliInvoker()
    bootstrap._clear_for_tests()
    try:
        root = bootstrap.build_root_app(builtins=(SPEC,), externals=())
        result = runner.invoke(
            root.meta,
            ["workspace", "init", "prod", "--path", str(tmp_path / "ws"), "-q", "-f", "raw"],
        )
    finally:
        bootstrap._clear_for_tests()

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert result.stdout.strip() == "prod"
