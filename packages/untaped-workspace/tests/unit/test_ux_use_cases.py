"""Unit tests for path/shell-init/edit use cases."""

from pathlib import Path

import pytest
from untaped_workspace.application import EditWorkspace, ShellInit, WorkspacePath
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import RegistryError, WorkspaceError


class _StubRegistry:
    def __init__(self, entries: list[Workspace]) -> None:
        self.entries = entries

    def get(self, name: str) -> Workspace:
        for w in self.entries:
            if w.name == name:
                return w
        raise RegistryError(f"unknown workspace: {name!r}")


def test_workspace_path_returns_registered_path(tmp_path: Path) -> None:
    registry = _StubRegistry([Workspace(name="prod", path=tmp_path / "prod")])
    assert WorkspacePath(registry)("prod") == tmp_path / "prod"


def test_workspace_path_unknown_raises(tmp_path: Path) -> None:
    registry = _StubRegistry([])
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


def test_edit_uses_visual_then_editor_then_vi(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def _runner(cmd):  # type: ignore[no-untyped-def]
        captured.append(list(cmd))
        return 0

    registry = _StubRegistry([Workspace(name="prod", path=tmp_path / "prod")])

    # explicit override wins
    EditWorkspace(registry, runner=_runner, env={})("prod", editor="code")
    assert captured[-1] == ["code", str(tmp_path / "prod")]

    # VISUAL beats EDITOR
    EditWorkspace(registry, runner=_runner, env={"VISUAL": "subl", "EDITOR": "vi"})("prod")
    assert captured[-1] == ["subl", str(tmp_path / "prod")]

    # EDITOR fallback
    EditWorkspace(registry, runner=_runner, env={"EDITOR": "nvim"})("prod")
    assert captured[-1] == ["nvim", str(tmp_path / "prod")]

    # default
    EditWorkspace(registry, runner=_runner, env={})("prod")
    assert captured[-1] == ["vi", str(tmp_path / "prod")]


def test_edit_missing_editor_raises(tmp_path: Path) -> None:
    def _runner(_cmd):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("no such file")

    registry = _StubRegistry([Workspace(name="prod", path=tmp_path / "prod")])
    with pytest.raises(WorkspaceError, match="editor not found"):
        EditWorkspace(registry, runner=_runner, env={})("prod", editor="bogus-editor")
