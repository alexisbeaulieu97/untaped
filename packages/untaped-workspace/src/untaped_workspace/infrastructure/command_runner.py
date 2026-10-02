"""Run a command in a directory with a timeout, capturing its output."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from typing import TYPE_CHECKING

from untaped_workspace.domain.models import CommandResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode(errors="replace")


def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)


def _stop_group(proc: subprocess.Popen[bytes]) -> None:
    """Stop what is left of an exited command's group: SIGTERM, a short wait, SIGKILL."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError, PermissionError:
        return  # nothing left: the usual case
    try:
        deadline = time.monotonic() + _TERM_WAIT_S
        while time.monotonic() < deadline:
            os.killpg(proc.pid, 0)
            time.sleep(0.02)
    except ProcessLookupError, PermissionError:
        pass
    finally:
        _signal_group(proc, signal.SIGKILL)


_REAP_TIMEOUT_S = 5.0
_GRACE_S = 2.0
_TERM_WAIT_S = 0.5
_POLL_S = 0.1


class SubprocessRunner:
    """:class:`CommandRunner` backed by ``Popen``; stdin is ``/dev/null``.

    The command runs in its own session so a timeout can kill its whole process tree.
    After :meth:`cancel` the runner starts nothing new.
    """

    def __init__(self) -> None:
        self._live: set[subprocess.Popen[bytes]] = set()
        self._cancelled = False
        self._lock = threading.Lock()

    def active_count(self) -> int:
        """How many commands are running right now."""
        with self._lock:
            return len(self._live)

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        """Stop every running command: SIGTERM its group, then SIGKILL it after ~2s."""
        with self._lock:
            self._cancelled = True
            procs = list(self._live)
        try:
            for proc in procs:
                _signal_group(proc, signal.SIGTERM)
            deadline = time.monotonic() + _GRACE_S
            while time.monotonic() < deadline and any(p.poll() is None for p in procs):
                time.sleep(0.02)
        finally:  # a second Ctrl-C during the grace wait must not skip the kill
            # A dead leader can leave live grandchildren holding the pipes: kill every group.
            for proc in procs:
                _signal_group(proc, signal.SIGKILL)

    def run(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
    ) -> CommandResult:
        start = time.monotonic()
        if self._is_cancelled():
            return CommandResult(
                returncode=None,
                stdout="",
                stderr="",
                duration_s=0.0,
                timed_out=False,
                cancelled=True,
            )
        try:
            if not argv:
                raise ValueError("empty command")
            proc = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env={**os.environ, **env},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            return _result(127, "", f"{exc}\n", start, timed_out=False)
        with self._lock:
            self._live.add(proc)
            if self._cancelled:  # cancel() ran between Popen and registration
                _signal_group(proc, signal.SIGKILL)
        try:
            return self._collect(proc, timeout, start)
        finally:
            with self._lock:
                self._live.discard(proc)

    @staticmethod
    def _collect(proc: subprocess.Popen[bytes], timeout: float, start: float) -> CommandResult:
        """Wait for the output; past the timeout, or a grace after the leader exits, kill.

        Once the command itself exits, whatever is left of its process group is
        stopped. A background process still holding the pipes open first gets
        :data:`_GRACE_S`. The command's own exit status is reported.
        """
        deadline = start + timeout
        exited_at: float | None = None
        try:
            while True:
                now = time.monotonic()
                limit = deadline if exited_at is None else min(deadline, exited_at + _GRACE_S)
                try:
                    raw_out, raw_err = proc.communicate(timeout=max(0.0, min(limit - now, _POLL_S)))
                except subprocess.TimeoutExpired:
                    if exited_at is None and proc.poll() is not None:
                        exited_at = time.monotonic()
                    if time.monotonic() >= limit:
                        break
                else:
                    _stop_group(proc)  # a background process may have closed the pipes
                    return _result(
                        proc.returncode, _text(raw_out), _text(raw_err), start, timed_out=False
                    )
        except BaseException:
            _signal_group(proc, signal.SIGKILL)
            raise
        if exited_at is None:
            _signal_group(proc, signal.SIGKILL)
            return _result(None, *_drain(proc), start, timed_out=True)
        _stop_group(proc)
        return _result(proc.returncode, *_drain(proc), start, timed_out=False)


def _drain(proc: subprocess.Popen[bytes]) -> tuple[str, str]:
    """Output of a killed process; closes the pipes if a stray holder keeps them open."""
    try:
        out, err = proc.communicate(timeout=_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        return _text(exc.stdout), _text(exc.stderr)
    return _text(out), _text(err)


def _result(
    returncode: int | None, stdout: str, stderr: str, start: float, *, timed_out: bool
) -> CommandResult:
    return CommandResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_s=time.monotonic() - start,
        timed_out=timed_out,
    )
