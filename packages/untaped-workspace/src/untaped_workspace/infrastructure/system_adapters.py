"""Concrete adapters for shell-out, editor-launch, and filesystem ops.

Application use cases depend on the small Protocols defined alongside
them; the default implementations live here so ``application/`` never
imports ``subprocess`` or ``shutil`` directly.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

ShellRunner = Callable[[str, Path], "subprocess.CompletedProcess[str]"]
"""Run a shell command in ``cwd`` and return the completed process."""

EditorRunner = Callable[[Sequence[str]], int]
"""Spawn an editor (argv) and return its exit code."""


class Filesystem(Protocol):
    """Side-effecting filesystem operations that need stubbing in tests."""

    def rmtree(self, path: Path) -> None: ...


def shell_runner(cmd: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Default :data:`ShellRunner`: run ``cmd`` via the shell, capture output.

    ``shell=True`` is intentional: ``workspace foreach`` accepts user-authored
    command strings that rely on shell features (pipes, redirects, glob
    expansion). **``cmd`` must come from a trusted source** — the user's own
    CLI input or a workspace manifest the user controls. Never thread
    third-party or externally-fetched content through this runner without
    sanitising / argv-quoting first; ``shell=True`` makes shell injection
    (CWE-78) trivial otherwise.
    """
    return subprocess.run(cmd, shell=True, cwd=cwd, text=True, capture_output=True, check=False)


def editor_runner(cmd: Sequence[str]) -> int:
    """Default :data:`EditorRunner`: spawn ``cmd`` and return its exit code."""
    completed = subprocess.run(list(cmd), check=False)
    return completed.returncode


class LocalFilesystem:
    """Default :class:`Filesystem` that delegates to :mod:`shutil`."""

    def rmtree(self, path: Path) -> None:
        shutil.rmtree(path)


__all__ = [
    "EditorRunner",
    "Filesystem",
    "LocalFilesystem",
    "ShellRunner",
    "editor_runner",
    "shell_runner",
]
