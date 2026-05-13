"""Unit tests for path/shell-init/edit use cases."""

from pathlib import Path

import pytest
from conftest import StubRegistry
from untaped_workspace.application import EditWorkspace, ShellInit, WorkspacePath
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import RegistryError, WorkspaceError


def test_workspace_path_returns_registered_path(tmp_path: Path) -> None:
    registry = StubRegistry([Workspace(name="prod", path=tmp_path / "prod")])
    assert WorkspacePath(registry)("prod") == tmp_path / "prod"


def test_workspace_path_unknown_raises(tmp_path: Path) -> None:
    registry = StubRegistry([])
    with pytest.raises(RegistryError):
        WorkspacePath(registry)("missing")


def test_shell_init_zsh() -> None:
    out = ShellInit()("zsh")
    assert "uwcd()" in out
    assert "cd " in out


def test_shell_init_bash() -> None:
    out = ShellInit()("bash")
    assert "uwcd()" in out


def test_shell_init_fish() -> None:
    out = ShellInit()("fish")
    assert "function uwcd" in out


def test_shell_init_unknown_shell() -> None:
    with pytest.raises(WorkspaceError, match="unsupported shell"):
        ShellInit()("powershell")


def test_edit_appends_workspace_path_to_argv(tmp_path: Path) -> None:
    """``EditWorkspace`` is now a thin "look up path, append, dispatch"
    use case — no env reading, no shlex parsing, no platform branching.
    Editor resolution lives in
    :func:`untaped_workspace.infrastructure.system_adapters.resolve_editor_argv`
    and is tested there. This test pins the use case's narrow contract:
    given an argv tuple, append the workspace path and call the runner."""
    captured: list[list[str]] = []

    def _runner(cmd):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return 0

    registry = StubRegistry([Workspace(name="prod", path=tmp_path / "prod")])
    rc = EditWorkspace(registry, runner=_runner)("prod", argv=("code", "--reuse-window"))
    assert rc == 0
    assert captured[-1] == ["code", "--reuse-window", str(tmp_path / "prod")]


def test_edit_missing_editor_raises(tmp_path: Path) -> None:
    """``FileNotFoundError`` from the runner — same shape ``subprocess``
    raises when the executable doesn't exist — surfaces as
    ``WorkspaceError`` naming the executable (argv[0]), not the full
    string with flags."""

    def _runner(_cmd):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("no such file")

    registry = StubRegistry([Workspace(name="prod", path=tmp_path / "prod")])
    with pytest.raises(WorkspaceError, match=r"editor not found: code$"):
        EditWorkspace(registry, runner=_runner)("prod", argv=("code", "--reuse-window"))
