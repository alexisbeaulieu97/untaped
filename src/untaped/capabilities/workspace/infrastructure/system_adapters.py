"""Concrete adapters for shell-out, editor-launch, and filesystem ops.

The Protocols and Callable aliases live next to the application use
cases that consume them
(:mod:`untaped.capabilities.workspace.application.ports`); this module hosts only
the default implementations so ``application/`` never imports
``subprocess``, ``shutil``, or process-state modules like ``os`` /
``shlex`` directly.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from untaped.capabilities.workspace.domain import DEFAULT_FOREACH_TIMEOUT
from untaped.capabilities.workspace.errors import WorkspaceError

_FOREACH_TIMEOUT_RETURN_CODE = 124
_INTERRUPTED_RETURN_CODE = 130
_FOREACH_TERMINATE_GRACE_SECONDS = 0.2
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


def shell_runner(cmd: str, cwd: Path, *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Default :data:`ShellRunner`: run ``cmd`` via the shell, capture output.

    ``shell=True`` is intentional: ``workspace foreach`` accepts user-authored
    command strings that rely on shell features (pipes, redirects, glob
    expansion). **``cmd`` must come from a trusted source** — the user's own
    CLI input or a workspace manifest the user controls. Never thread
    third-party or externally-fetched content through this runner without
    sanitising / argv-quoting first; ``shell=True`` makes shell injection
    (CWE-78) trivial otherwise.
    """
    return _communicate(_spawn(cmd, cwd), cmd, timeout)


class InterruptibleShellRunner:
    """A :data:`ShellRunner` that can stop every command it is running.

    Commands run in their own session, so the terminal's SIGINT never
    reaches them, and under ``foreach --parallel`` they run on worker
    threads that never see ``KeyboardInterrupt``. The calling thread calls
    :meth:`terminate_all` on interrupt: every live process group gets
    SIGTERM, then SIGKILL after a short grace, and commands not yet started
    are refused. Thread-safe; use one instance per run.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: set[subprocess.Popen[str]] = set()
        self._closed = False

    def __call__(self, cmd: str, cwd: Path, *, timeout: float) -> subprocess.CompletedProcess[str]:
        with self._lock:
            if self._closed:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=_INTERRUPTED_RETURN_CODE, stdout="", stderr="interrupted"
                )
            process = _spawn(cmd, cwd)
            self._live.add(process)
        try:
            return _communicate(process, cmd, timeout)
        finally:
            with self._lock:
                self._live.discard(process)

    def terminate_all(self) -> None:
        """Refuse new commands and tear down every running process group."""
        with self._lock:
            self._closed = True
            live = list(self._live)
        for process in live:
            _signal_process_group(process, signal.SIGTERM)
        deadline = time.monotonic() + _FOREACH_TERMINATE_GRACE_SECONDS
        while time.monotonic() < deadline and any(p.poll() is None for p in live):
            time.sleep(0.01)
        for process in live:
            if os.name == "nt":
                if process.poll() is None:
                    process.kill()
                continue
            # The shell may exit on TERM while descendants ignore it; with
            # ``start_new_session`` the group id is the leader's pid even
            # after the leader has been reaped.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, _KILL_SIGNAL)


def _spawn(cmd: str, cwd: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        cmd,
        shell=True,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    )


def _communicate(
    process: subprocess.Popen[str], cmd: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _signal_process_group(process, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=_FOREACH_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _signal_process_group(process, _KILL_SIGNAL)
                stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=_FOREACH_TIMEOUT_RETURN_CODE,
                stdout=stdout or "",
                stderr=_append_timeout_message(stderr or "", timeout),
            )
    except BaseException:
        # Ctrl-C (or any other escape): the child runs in its own session,
        # so the terminal's SIGINT never reached it. Tear the group down
        # before propagating so no command keeps running unattended.
        _terminate_process_group(process)
        raise
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=_completed_returncode(process),
        stdout=stdout or "",
        stderr=stderr or "",
    )


def _completed_returncode(process: subprocess.Popen[str]) -> int:
    # `communicate()` has completed before this helper is called, but
    # `Popen.returncode` remains typed as optional.
    return process.returncode if process.returncode is not None else 0


def _signal_process_group(process: subprocess.Popen[str], sig: signal.Signals | int) -> None:
    if os.name == "nt":
        if sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(process.pid), sig)


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """SIGTERM the group, wait briefly, then SIGKILL and reap."""
    _signal_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=_FOREACH_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, _KILL_SIGNAL)
        process.wait()
    else:
        # The shell may exit on TERM while descendants ignore it. After
        # the leader is reaped ``getpgid`` fails, but with
        # ``start_new_session`` the group id is the leader's pid.
        if os.name != "nt":
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, _KILL_SIGNAL)


def _append_timeout_message(stderr: str, timeout: float) -> str:
    message = f"timed out after {timeout:g}s"
    if stderr and not stderr.endswith("\n"):
        return f"{stderr}\n{message}"
    return f"{stderr}{message}"


def resolve_editor_argv(editor: str, *, posix: bool | None = None) -> tuple[str, ...]:
    """Split an explicit ``--editor`` command into argv.

    Without ``--editor``, ``run_editor`` reads ``$VISUAL`` / ``$EDITOR``
    itself and fails with a hint when neither is set. The command is split
    with :func:`shlex.split`; ``posix=os.name != "nt"`` by default, so
    Windows paths with backslashes survive (POSIX mode would mangle
    ``C:\\Tools\\vim.exe``). ``posix`` is injectable so unit tests cover
    both branches without depending on the runner's OS.

    Raises :class:`WorkspaceError` on an empty selection (e.g. whitespace
    only) and on :class:`shlex.split` ``ValueError`` (unterminated
    quoting), so callers see one error shape regardless of the failure
    mode.
    """
    use_posix = posix if posix is not None else os.name != "nt"
    try:
        argv = shlex.split(editor, posix=use_posix)
    except ValueError as exc:
        raise WorkspaceError(f"could not parse editor command {editor!r}: {exc}") from exc
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

    Destructive operations raise :class:`WorkspaceError` instead of a raw
    :class:`OSError` so users get an error line, not a traceback.
    ``rmtree`` clears read-only bits and retries (git marks object files
    read-only, which blocks deletion on Windows).
    """

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def mkdir(self, path: Path, *, parents: bool, exist_ok: bool) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def iterdir(self, path: Path) -> Iterator[Path]:
        return path.iterdir()

    def rmtree(self, path: Path) -> None:
        try:
            shutil.rmtree(path, onexc=_retry_writable)
        except OSError as exc:
            raise WorkspaceError(f"could not remove {path}: {exc}") from exc

    def unlink(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError as exc:
            raise WorkspaceError(f"could not remove {path}: {exc}") from exc

    def rmdir(self, path: Path) -> None:
        try:
            path.rmdir()
        except OSError as exc:
            raise WorkspaceError(f"could not remove {path}: {exc}") from exc


def _retry_writable(func: Callable[..., object], path: str, exc: BaseException) -> None:
    """``shutil.rmtree`` ``onexc``: make ``path`` (and its parent) writable, retry once."""
    if not isinstance(exc, PermissionError):
        raise exc
    for target, bits in ((os.path.dirname(path), stat.S_IRWXU), (path, stat.S_IWRITE)):
        with contextlib.suppress(OSError):
            mode = os.lstat(target).st_mode
            # Never chmod through a symlink: that would touch its target.
            if not stat.S_ISLNK(mode):
                os.chmod(target, stat.S_IMODE(mode) | bits)
    func(path)


__all__ = [
    "DEFAULT_FOREACH_TIMEOUT",
    "InterruptibleShellRunner",
    "LocalFilesystem",
    "resolve_editor_argv",
    "shell_runner",
]
