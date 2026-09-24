"""Unit tests for path/shell-init/edit use cases."""

from pathlib import Path

import pytest

from untaped.capabilities.workspace.application import ShellInit, WorkspacePath
from untaped.capabilities.workspace.domain import Workspace
from untaped.capabilities.workspace.errors import RegistryError, WorkspaceError
from workspace.conftest import StubRegistry


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
    assert "_uwcd_complete()" in out
    assert "compdef _uwcd_complete uwcd" in out
    assert "untaped workspace list --format raw --columns name 2>/dev/null" in out


def test_shell_init_bash() -> None:
    out = ShellInit()("bash")
    assert "uwcd()" in out
    assert "_uwcd_complete()" in out
    assert "complete -F _uwcd_complete uwcd" in out
    assert "untaped workspace list --format raw --columns name 2>/dev/null" in out


def test_shell_init_fish() -> None:
    out = ShellInit()("fish")
    assert "function uwcd" in out
    assert "function __uwcd_workspaces" in out
    assert "complete -c uwcd" in out
    assert "untaped workspace list --format raw --columns name 2>/dev/null" in out


def test_shell_init_unknown_shell() -> None:
    with pytest.raises(WorkspaceError, match="unsupported shell"):
        ShellInit()("powershell")
