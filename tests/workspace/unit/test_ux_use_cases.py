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


_LIST_CMD = "untaped workspace list --format raw --columns name 2>/dev/null"


@pytest.mark.parametrize(
    ("shell", "fragments"),
    [
        ("zsh", ["uwcd()", "_uwcd_complete()", "compdef _uwcd_complete uwcd", _LIST_CMD]),
        ("bash", ["uwcd()", "_uwcd_complete()", "complete -F _uwcd_complete uwcd", _LIST_CMD]),
        ("fish", ["function uwcd", "function __uwcd_workspaces", "complete -c uwcd", _LIST_CMD]),
    ],
)
def test_shell_init_snippets(shell: str, fragments: list[str]) -> None:
    out = ShellInit()(shell)
    assert [f for f in fragments if f not in out] == []


def test_shell_init_sh_is_the_posix_function_only() -> None:
    out = ShellInit()("sh")
    assert "uwcd()" in out
    assert "complete" not in out


def test_shell_init_unknown_shell() -> None:
    with pytest.raises(WorkspaceError, match="unsupported shell"):
        ShellInit()("powershell")
