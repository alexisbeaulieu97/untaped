"""Child process groups and untaped's own signals: the private half of :mod:`untaped.sdk.process`.

Every child the block starts runs in its own session, so its process group
holds it and every helper it starts, and nothing it starts can prompt on the
terminal. A signal sent to untaped's group (Ctrl-C, a closed terminal,
``timeout``, a cancelled CI job) then no longer reaches the child: the handler
:func:`install` puts in place passes SIGINT, SIGTERM and SIGHUP on to every
registered group and raises one plain ``KeyboardInterrupt`` on the main
thread, so every ``except KeyboardInterrupt`` cleanup runs for all three
(``Popen.__exit__`` treats only that exact type as an interrupt, and then
stops waiting for its child). Closing records which signal it was.

A group gets one soft signal, ever, and SIGKILL one grace after it (many
tools read a second interrupt as "force quit now"). Each group's clock is
claimed with one dict operation, so the handler, a run and the exit net never
send two. The handler takes no lock and never waits.

*Closing* exists once :func:`install` ran: after an ending signal (or at exit)
a worker thread starts no new child. The main thread may: only cleanup runs
there by then. A signal that lands while the main thread itself is starting a
child is deferred until the child is registered, then replayed, so the
interrupt surfaces where the group gets stopped.

Only core installs signal handlers, at the entry point; ``tests/repo`` and
lint's ``process-group`` and ``signal-handler`` rules keep it that way.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import FrameType
from typing import Any

#: Seconds between a group's soft signal and its SIGKILL.
GRACE_S = 2.0
#: How often a waiting run looks at its clock, its ``stop`` and its deadline.
POLL_S = 0.1
#: Process groups exist (POSIX); without them only the leader is stopped (#1013).
HAS_PROCESS_GROUPS = hasattr(os, "killpg")
_ENDING: tuple[int, ...] = tuple(
    sig for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if sig
)
_HANDLED: tuple[int, ...] = (signal.SIGINT, *_ENDING)
_KILL: int = getattr(signal, "SIGKILL", signal.SIGTERM)


class _Clock:
    """When a group got its soft signal; a fresh object, so ``setdefault`` tells who claimed it."""

    __slots__ = ("started",)

    def __init__(self) -> None:
        self.started = time.monotonic()


class _State:
    """Process-wide; every read and write is one dict or attribute operation (no lock)."""

    def __init__(self) -> None:
        self.groups: dict[int, subprocess.Popen[bytes]] = {}  # group id -> its leader
        self.clocks: dict[int, _Clock] = {}
        self.closing: tuple[int, float] | None = None  # (signal, 0 for exit; when)
        self.spawning: dict[int, None] = {}  # worker threads between their mark and Popen's end
        self.main_spawning = False
        self.deferred: list[int] = []
        self.installed = False
        self.previous: dict[int, Any] = {}
        self.net = False
        self.netting = False  # the exit net runs: a signal is recorded, not raised


_S = _State()


def _after_fork_in_child() -> None:
    """A forked child starts with no groups, marks or closing: its exit net spares the parent's."""
    _S.groups, _S.clocks, _S.spawning, _S.deferred = {}, {}, {}, []
    _S.closing, _S.main_spawning, _S.netting = None, False, False


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


# ── groups ──────────────────────────────────────────────────────────────────


def _signal(pgid: int, sig: int) -> bool:
    """Send ``sig`` to group ``pgid``; ``False`` when no process is left in it."""
    if not HAS_PROCESS_GROUPS:
        leader = _S.groups.get(pgid)
        if leader is None or leader.poll() is not None:
            return False
        with contextlib.suppress(OSError):
            leader.kill() if sig == _KILL else leader.terminate()
        return True
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError, PermissionError:  # macOS: EPERM for a group of zombies
        return False
    return True


def _claim(pgid: int) -> bool:
    """Start ``pgid``'s clock; ``True`` only for the one caller that started it."""
    clock = _Clock()
    return _S.clocks.setdefault(pgid, clock) is clock


def soft_signal(pgid: int, sig: int = signal.SIGTERM) -> None:
    """Give ``pgid`` its one soft signal, unless it had it already."""
    if _claim(pgid):
        _signal(pgid, sig)


def clock_age(pgid: int) -> float | None:
    """Seconds since ``pgid``'s soft signal, ``None`` before it."""
    clock = _S.clocks.get(pgid)
    return None if clock is None else time.monotonic() - clock.started


def registered(pgid: int) -> bool:
    """Whether group ``pgid`` is still untaped's to signal."""
    return pgid in _S.groups


def forget(pgid: int) -> None:
    """Deregister group ``pgid``: nothing signals it from now on."""
    _S.groups.pop(pgid, None)
    _S.clocks.pop(pgid, None)


def _reap_leftovers(pgid: int) -> None:
    """Reap zombies of ``pgid`` that became untaped's children (untaped as PID 1 or a subreaper)."""
    with contextlib.suppress(ChildProcessError, OSError):
        while os.waitpid(-pgid, os.WNOHANG)[0]:
            pass


def emptied(process: subprocess.Popen[bytes]) -> bool:
    """Whether ``process`` exited and nothing is left in its group (reaps the leader).

    An emptied group is deregistered in the same step, so its id is never
    signalled after another process could have taken it; a deregistered
    group's id is never probed or reaped again.
    """
    if process.poll() is None:
        return False
    if not registered(process.pid):
        return True
    if HAS_PROCESS_GROUPS:
        _reap_leftovers(process.pid)
        if _signal(process.pid, 0):
            return False
    forget(process.pid)
    return True


def stop(process: subprocess.Popen[bytes]) -> None:
    """Stop ``process``'s group: SIGTERM unless it had its soft signal, SIGKILL one grace on.

    The SIGKILL is in a ``finally``, so a second Ctrl-C during the grace
    still reaches it. Reaps the leader and deregisters the group. A group no
    longer registered is left alone: its id may be another process's by now.
    """
    pgid = process.pid
    if not registered(pgid):
        return
    soft_signal(pgid)
    clock = _S.clocks.get(pgid)
    deadline = (clock.started if clock is not None else time.monotonic()) + GRACE_S
    gone = False
    try:
        while not (gone := emptied(process)) and (left := deadline - time.monotonic()) > 0:
            time.sleep(min(0.02, left))
    finally:
        if not gone and registered(pgid):  # an interrupt may land after emptied() forgot it
            try:
                _signal(pgid, _KILL)
                with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                    process.wait(timeout=GRACE_S)
                if HAS_PROCESS_GROUPS:
                    _reap_leftovers(pgid)
            finally:
                forget(pgid)


# ── starting a child ────────────────────────────────────────────────────────


def spawn(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    stdin: int,
) -> subprocess.Popen[bytes] | None:
    """Start ``argv`` in its own session with piped output and register its group.

    ``None`` when untaped is closing and this is a worker thread. A worker
    marks itself before it reads closing and reads it again once its group is
    registered, so either the handler sees the group or the worker sees
    closing. The main thread's mark defers a signal until the group is
    registered, then replays it: an interrupt it raises stops this group.
    """
    main = threading.current_thread() is threading.main_thread()
    me = threading.get_ident()
    if main:
        _S.main_spawning = True
    else:
        _S.spawning[me] = None
        if _S.closing is not None:
            _S.spawning.pop(me, None)
            return None
    process: subprocess.Popen[bytes] | None = None
    try:
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=None if env is None else dict(env),
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=HAS_PROCESS_GROUPS,
            )
            _S.groups[process.pid] = process
        finally:
            if main:
                _S.main_spawning = False
                _replay_deferred()  # may raise: the except below stops it
            else:
                _S.spawning.pop(me, None)
        closing = _S.closing
        if not main and closing is not None:  # started before the signal: give it the signal too
            soft_signal(process.pid, closing[0] or signal.SIGTERM)
        _exit_net_registered()  # a call: an interrupt can land in it, so inside the try
    except BaseException:
        if process is not None:
            abandon(process)
        raise
    return process


def abandon(process: subprocess.Popen[bytes]) -> None:
    """Stop ``process``'s group and close its pipes: an exception cut its run short.

    The caller's ``try`` opens before any call after the group exists, so an
    interrupt cannot land between them (a call is where Python raises one).
    """
    stop(process)
    close_pipes(process)


def close_pipes(process: subprocess.Popen[bytes]) -> None:
    """Close the pipes untaped holds to ``process``."""
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None:
            with contextlib.suppress(OSError):
                pipe.close()


# ── signals ─────────────────────────────────────────────────────────────────


def ending(signum: int, *, closing: bool = True) -> bool:
    """Pass ``signum`` on to every group that had no soft signal yet; ``False`` for a repeat.

    The handler's steps short of raising, for a screen that caught the
    signal or the ctrl-c key itself. Once installed, it also sets closing
    (unless ``closing`` is false); a SIGTERM or SIGHUP within one grace of
    the one that set it is the same event (a closing terminal sends two), and
    one later ends untaped now.
    """
    now = time.monotonic()
    current = _S.closing
    if _S.installed and current is not None and signum in _ENDING:
        if now - current[1] >= GRACE_S:
            _end_now(signum)
        if current[0] in _ENDING:
            return False
    if _S.installed and closing and current is None:
        _S.closing = (signum, now)
    for pgid in list(_S.groups):
        if _claim(pgid):
            _signal(pgid, signum)
    return True


def _replay_deferred() -> None:
    """Replay every signal deferred during a main-thread spawn; raise once if any ends untaped."""
    deferred, _S.deferred = _S.deferred, []
    raised = False
    for signum in deferred:
        raised = _handle(signum, None) or raised
    if raised:
        raise KeyboardInterrupt


def _on_signal(signum: int, frame: FrameType | None) -> None:
    if _S.main_spawning:
        _S.deferred.append(signum)  # replayed once the main thread's child is registered
        return
    if _handle(signum, frame):
        raise KeyboardInterrupt


def _handle(signum: int, frame: FrameType | None) -> bool:
    """The handler short of raising: ``True`` when it should raise the interrupt."""
    if _S.netting:  # exiting already: the net ends untaped by an ending signal
        current = _S.closing
        if signum in _ENDING and (current is None or current[0] not in _ENDING):
            _S.closing = (signum, time.monotonic())
        return False
    previous = _S.previous.get(signum)
    if callable(previous) and previous is not signal.default_int_handler:
        # Installed before untaped: it decides whether untaped ends, as before.
        ending(signum, closing=False)
        previous(signum, frame)
        return False
    return ending(signum)


def _end_now(signum: int) -> None:
    """SIGKILL every group and die of ``signum`` without waiting for anything."""
    for pgid in list(_S.groups):
        _signal(pgid, _KILL)
    _die(signum)


def _die(signum: int) -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    os._exit(128 + signum)  # still here: PID 1 ignores its own default signals


def install() -> None:
    """Handle SIGINT, SIGTERM and SIGHUP for every child: the CLI entry point's call, once.

    A signal untaped ignores (``nohup``, a background job) stays ignored. Off
    the main thread, or without process groups (Windows, #1013), this does nothing.
    """
    if not HAS_PROCESS_GROUPS or threading.current_thread() is not threading.main_thread():
        return
    for sig in _HANDLED:
        previous = signal.getsignal(sig)
        if previous is None or previous == signal.SIG_IGN or previous is _on_signal:
            continue
        _S.previous[sig] = previous
        signal.signal(sig, _on_signal)
    _S.installed = True
    _exit_net_registered()


# ── the exit net ────────────────────────────────────────────────────────────


def _exit_net_registered() -> None:
    if not _S.net:
        _S.net = True
        atexit.register(_exit_net)


def _exit_net() -> None:
    """At exit: stop every group nobody closed, then end by SIGTERM or SIGHUP if that started it.

    Registered at the entry point before anything else can, so it runs after
    every later hook. Python joined the worker threads already; what is left
    is what nobody closed and the children of daemon threads.
    """
    _S.netting = True
    if _S.installed and _S.closing is None:
        _S.closing = (0, time.monotonic())
    deadline = time.monotonic() + GRACE_S
    while _S.spawning and time.monotonic() < deadline:  # a worker between its mark and Popen
        time.sleep(0.01)
    leaders = list(_S.groups.values())
    for leader in leaders:
        soft_signal(leader.pid)
    try:
        while [p for p in leaders if not emptied(p)] and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        for leader in leaders:
            if leader.pid in _S.groups:
                stop(leader)  # each clock is past or near its grace: SIGKILL what is left
    closing = _S.closing
    if _S.installed and closing is not None and closing[0] in _ENDING:
        with contextlib.suppress(Exception):
            logging.shutdown()
        _die(closing[0])
