"""Concrete adapters for shell-out, editor-launch, and filesystem ops.

The Protocols and Callable aliases live next to the application use
cases that consume them
(:mod:`untaped_workspace.application.ports`); this module hosts only
the default implementations so ``application/`` never imports
``subprocess`` or ``shutil`` directly.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path


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
    """Default :class:`Filesystem` that delegates to :mod:`pathlib` / :mod:`shutil`.

    Each method is a thin pass-through to the equivalent
    :class:`pathlib.Path` operation (or :func:`shutil.rmtree` for the
    recursive delete). The port exists so application use cases never
    import :mod:`pathlib` or :mod:`shutil` for I/O — all disk reads and
    writes flow through this single seam, which tests stub.
    """

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def mkdir(self, path: Path, *, parents: bool = True, exist_ok: bool = True) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def iterdir(self, path: Path) -> Iterator[Path]:
        return path.iterdir()

    def rmtree(self, path: Path) -> None:
        shutil.rmtree(path)


__all__ = [
    "LocalFilesystem",
    "editor_runner",
    "shell_runner",
]
