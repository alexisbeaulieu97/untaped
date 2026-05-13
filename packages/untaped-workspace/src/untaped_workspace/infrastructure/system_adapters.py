"""Concrete adapters for shell-out, editor-launch, and filesystem ops.

The Protocols and Callable aliases live next to the application use
cases that consume them
(:mod:`untaped_workspace.application.ports`); this module hosts only
the default implementations so ``application/`` never imports
``subprocess``, ``shutil``, or process-state modules like ``os`` /
``shlex`` directly.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from untaped_workspace.errors import WorkspaceError


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


def resolve_editor_argv(
    editor: str | None,
    *,
    env: Mapping[str, str] | None = None,
    posix: bool | None = None,
) -> tuple[str, ...]:
    """Resolve ``--editor`` / ``$VISUAL`` / ``$EDITOR`` / ``"vi"`` to argv.

    Precedence: explicit ``editor`` argument > ``$VISUAL`` > ``$EDITOR``
    > the literal ``"vi"`` fallback. The selected command is split with
    :func:`shlex.split`; ``posix=os.name != "nt"`` by default, so
    Windows paths with backslashes survive (POSIX mode would mangle
    ``C:\\Tools\\vim.exe``). Both ``env`` and ``posix`` are injectable
    so unit tests cover both branches without depending on the runner's
    OS.

    Raises :class:`WorkspaceError` on an empty selection (e.g. whitespace
    only) and on :class:`shlex.split` ``ValueError`` (unterminated
    quoting), so callers see one error shape regardless of the failure
    mode.
    """
    environment: Mapping[str, str] = env if env is not None else os.environ
    use_posix = posix if posix is not None else os.name != "nt"
    chosen = editor or environment.get("VISUAL") or environment.get("EDITOR") or "vi"
    try:
        argv = shlex.split(chosen, posix=use_posix)
    except ValueError as exc:
        raise WorkspaceError(f"could not parse editor command {chosen!r}: {exc}") from exc
    if not argv:
        raise WorkspaceError("editor command is empty")
    return tuple(argv)


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

    def mkdir(self, path: Path, *, parents: bool, exist_ok: bool) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def iterdir(self, path: Path) -> Iterator[Path]:
        return path.iterdir()

    def rmtree(self, path: Path) -> None:
        shutil.rmtree(path)


__all__ = [
    "LocalFilesystem",
    "editor_runner",
    "resolve_editor_argv",
    "shell_runner",
]
