"""Child processes: run one to the end, or keep one running, and stop its whole group.

Every child runs in its own session: its process group holds it and every
helper it starts, so untaped can stop them all without stopping itself, and
nothing the child starts can prompt on the terminal. Stopping is one sequence
for every reason (a timeout, ``stop``, an exception, untaped's own SIGINT,
SIGTERM or SIGHUP, ``close()``): the group gets one soft signal (that
interrupt when it is the reason, SIGTERM otherwise), then SIGKILL two seconds
later. A child that exits on its own leaves what it started running, as a
shell leaves ``cmd &``, unless that still holds its output, which would
otherwise keep the run waiting: that is stopped two seconds after the child
exits.

Use this module for a child that runs in its own group, must stop as a tree,
or may run on a worker thread past an interrupt; lint's ``process-group``
rule flags a group made by hand, which would be left running when untaped is
interrupted. A short synchronous child in untaped's own group may stay a
plain ``subprocess`` call: Ctrl-C reaches it directly.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Literal

from pydantic import BaseModel, ConfigDict

import untaped.sdk._process_groups as groups
from untaped.sdk.errors import ErrorCategory, UntapedError
from untaped.sdk.marks import experimental

__all__ = ["ProcessResult", "RunningProcess", "run_process", "start_process"]

type Ended = Literal["exited", "timed_out", "stopped"]


@experimental
class ProcessResult(BaseModel):
    """How a child run by :func:`run_process` ended, and what it wrote.

    ``ended`` is ``exited`` when the child exited on its own (``returncode``
    is its status; negative: it died of that signal), ``timed_out`` when the
    timeout stopped it and ``stopped`` when ``stop`` or untaped's own
    interrupt did; ``returncode`` is ``None`` for both. ``stdout`` and
    ``stderr`` hold what was read before the end.

    First consumer: core's ``run_git``; then untaped-recipe's hook worker (#579).
    """

    model_config = ConfigDict(frozen=True)

    ended: Ended
    returncode: int | None
    stdout: bytes
    stderr: bytes
    duration_s: float


@experimental
def run_process(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input: bytes | None = None,
    stop: threading.Event | None = None,
) -> ProcessResult:
    """Run ``argv`` in its own group to the end, capturing both streams.

    ``env`` is the whole environment (``None`` inherits untaped's); stdin is
    ``input`` or ``/dev/null``. Setting ``stop`` stops the run within a poll
    step, and a call made after it is set starts nothing; one ``Event`` stops
    every run holding it (``on_abort=stop.set``). Returns within ``timeout``
    plus two graces, even against a process that left the group and keeps the
    output open. A program that cannot start raises ``OSError``, an empty
    ``argv`` ``ValueError``; any exception in the calling thread stops the
    group, then is raised again.

    First consumer: core's ``run_git``; then untaped-recipe's hook worker (#579).
    """
    if not argv:
        raise ValueError("run_process: argv is empty")
    started = time.monotonic()
    if stop is not None and stop.is_set():
        return _result("stopped", None, b"", b"", started)
    process = groups.spawn(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
    )
    if process is None:
        return _result("stopped", None, b"", b"", started)
    return _Run(process, timeout=timeout, stop=stop, started=started).collect(input)


class _Run:
    """One :func:`run_process` call between its start and its result."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout: float,
        stop: threading.Event | None,
        started: float,
    ) -> None:
        self.process = process
        self.deadline = started + timeout
        self.stop = stop
        self.started = started
        self.reason: Ended | Literal["leftover"] | None = None
        self.exited_at: float | None = None
        self.output: tuple[bytes | None, bytes | None] = (None, None)

    def collect(self, input: bytes | None) -> ProcessResult:
        process, pgid = self.process, self.process.pid
        try:
            done = self._pump(input, groups.POLL_S)
            while not done:
                self._decide()
                age = groups.clock_age(pgid)
                if age is not None and age >= groups.GRACE_S:
                    groups.stop(process)  # past the grace: SIGKILL what is left
                    self._drain()
                    break
                done = self._pump(None, groups.POLL_S)
            else:
                if self.reason is None and groups.clock_age(pgid) is not None:
                    self.reason = "stopped"  # it exited of untaped's forwarded signal
                if self.reason is None:
                    groups.forget(pgid)  # what it left holds no pipe: it runs on, as after `cmd &`
                else:
                    groups.stop(process)  # finish the stop sequence it started
        except BaseException:
            groups.stop(process)
            groups.close_pipes(process)
            raise
        out, err = self.output
        if self.reason in (None, "leftover"):
            return _result("exited", process.returncode, out, err, self.started)
        ended: Ended = "timed_out" if self.reason == "timed_out" else "stopped"
        return _result(ended, None, out, err, self.started)

    def _pump(self, input: bytes | None, seconds: float) -> bool:
        """Read for up to ``seconds``; ``True`` once the child exited and closed its output."""
        try:
            self.output = self.process.communicate(input, timeout=seconds)
        except subprocess.TimeoutExpired as exc:
            self.output = (_as_bytes(exc.stdout), _as_bytes(exc.stderr))
            return False
        return True

    def _decide(self) -> None:
        """Start stopping once there is a reason: a signal, ``stop``, the timeout, a leftover."""
        if self.reason is not None:
            return
        pgid, now = self.process.pid, time.monotonic()
        if self.exited_at is None and self.process.poll() is not None:
            self.exited_at = now
        if groups.clock_age(pgid) is not None:
            self.reason = "stopped"  # untaped's own signal reached the group
        elif self.stop is not None and self.stop.is_set():
            self.reason = "stopped"
        elif now >= self.deadline:
            self.reason = "timed_out"
        elif self.exited_at is not None and now - self.exited_at >= groups.GRACE_S:
            self.reason = "leftover"  # something it left holds its output
        if self.reason is not None:
            groups.soft_signal(pgid)

    def _drain(self) -> None:
        """What is left to read after the SIGKILL; closes the pipes if an escapee holds them."""
        if not self._pump(None, groups.GRACE_S):
            groups.close_pipes(self.process)


def _as_bytes(data: bytes | str | None) -> bytes:
    if data is None:
        return b""
    return data.encode() if isinstance(data, str) else data


def _result(
    ended: Ended, returncode: int | None, out: bytes | None, err: bytes | None, started: float
) -> ProcessResult:
    return ProcessResult(
        ended=ended,
        returncode=returncode,
        stdout=out or b"",
        stderr=err or b"",
        duration_s=time.monotonic() - started,
    )


@experimental
class RunningProcess:
    """A long-lived child with three pipes; :meth:`close` ends it and its group.

    The owner reads ``stdout`` and ``stderr`` and closes them once it sees end
    of file; :meth:`close` closes only ``stdin``. Safe from any thread.
    :func:`start_process` makes one; it has no public constructor.

    First consumer: untaped-recipe's hook worker (#579).
    """

    _process: subprocess.Popen[bytes]
    _lock: threading.Lock
    _closed: bool

    def __init__(self) -> None:
        raise TypeError("start a child with start_process()")

    @classmethod
    def _of(cls, process: subprocess.Popen[bytes]) -> RunningProcess:
        running = cls.__new__(cls)
        running._process, running._lock, running._closed = process, threading.Lock(), False
        return running

    @property
    def stdin(self) -> IO[bytes]:
        """The child's stdin."""
        assert self._process.stdin is not None
        return self._process.stdin

    @property
    def stdout(self) -> IO[bytes]:
        """The child's stdout."""
        assert self._process.stdout is not None
        return self._process.stdout

    @property
    def stderr(self) -> IO[bytes]:
        """The child's stderr."""
        assert self._process.stderr is not None
        return self._process.stderr

    def poll(self) -> int | None:
        """The child's exit status, ``None`` while it runs."""
        returncode = self._process.poll()
        if returncode is not None:
            groups.emptied(self._process)
        return returncode

    def wait(self, timeout: float) -> int | None:
        """Wait up to ``timeout`` seconds for the child to exit; ``None``: it still runs."""
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        return self.poll()

    def close(self) -> int | None:
        """Close stdin, give the child one grace to exit, then stop whatever is left of its group.

        Idempotent; returns the child's exit status, or ``None`` when it could
        not be reaped: SIGKILL could not end it (a process stuck in the
        kernel), or another interrupt cut the stop short. The stop runs even
        when the grace is interrupted.
        """
        with self._lock:
            if not self._closed:
                try:
                    self._closed = True
                    with contextlib.suppress(OSError):
                        self.stdin.close()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        self._process.wait(timeout=groups.GRACE_S)
                finally:
                    if not groups.emptied(self._process):
                        groups.stop(self._process)
        return self._process.returncode

    def __enter__(self) -> RunningProcess:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@experimental
def start_process(
    argv: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> RunningProcess:
    """Start ``argv`` in its own group with three pipes and return its handle.

    ``env`` is the whole environment (``None`` inherits untaped's). The
    owner bounds each exchange itself and ends the child with
    :meth:`RunningProcess.close` (or ``with``). Raises ``OSError`` when the
    program cannot start, ``ValueError`` for an empty ``argv``, and an
    ``interrupted`` :class:`~untaped.sdk.errors.UntapedError` on a worker
    thread once untaped is stopping.

    First consumer: untaped-recipe's hook worker (#579).
    """
    if not argv:
        raise ValueError("start_process: argv is empty")
    process = groups.spawn(argv, cwd=cwd, env=env, stdin=subprocess.PIPE)
    if process is None:
        raise UntapedError("untaped is stopping; not started", category=ErrorCategory.INTERRUPTED)
    return RunningProcess._of(process)
