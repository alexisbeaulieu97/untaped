"""The private half of the process block, one rule at a time and in-process.

``test_process.py`` drives these through real signals in child Pythons; these
tests swap the process-wide state for a fresh one and record what would be
sent, so each rule's branch runs where coverage sees it.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import Any

import pytest

from untaped.sdk import _process_groups
from untaped.sdk._process_groups import Interrupted

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process groups (#1013)")

GRACE = 0.3
SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


@pytest.fixture(autouse=True)
def state(monkeypatch: pytest.MonkeyPatch) -> _process_groups._State:
    fresh = _process_groups._State()
    fresh.net = True  # the real exit net is registered already; a fresh one would add another
    monkeypatch.setattr(_process_groups, "_S", fresh)
    monkeypatch.setattr(_process_groups, "GRACE_S", GRACE)
    return fresh


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    calls: list[tuple[int, int]] = []

    def record(pgid: int, sig: int) -> bool:
        calls.append((pgid, sig))
        return True

    monkeypatch.setattr(_process_groups, "_signal", record)
    return calls


@pytest.fixture
def died(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    deaths: list[int] = []

    def die(signum: int) -> None:
        deaths.append(signum)
        raise SystemExit(128 + signum)

    monkeypatch.setattr(_process_groups, "_die", die)
    return deaths


def _spawn(argv: list[str] = SLEEP) -> subprocess.Popen[bytes]:
    process = _process_groups.spawn(argv, cwd=None, env=None, stdin=subprocess.DEVNULL)
    assert process is not None
    return process


def _finish(process: subprocess.Popen[bytes]) -> None:
    _process_groups.stop(process)
    _process_groups.close_pipes(process)


# ── ending ──────────────────────────────────────────────────────────────────


def test_before_install_a_signal_is_forwarded_but_closes_nothing(
    state: _process_groups._State, sent: list[tuple[int, int]]
) -> None:
    state.groups[7] = None  # type: ignore[assignment]
    assert _process_groups.ending(signal.SIGTERM)
    assert sent == [(7, signal.SIGTERM)]
    assert state.closing is None


def test_the_first_ending_signal_closes_and_a_second_close_by_is_the_same_event(
    state: _process_groups._State, sent: list[tuple[int, int]]
) -> None:
    state.installed = True
    state.groups[7] = None  # type: ignore[assignment]
    assert _process_groups.ending(signal.SIGHUP)
    assert state.closing is not None and state.closing[0] == signal.SIGHUP
    state.groups[8] = None  # type: ignore[assignment]
    assert not _process_groups.ending(signal.SIGTERM)
    assert sent == [(7, signal.SIGHUP)]


def test_after_ctrl_c_a_sigterm_is_forwarded_to_groups_not_yet_signalled(
    state: _process_groups._State, sent: list[tuple[int, int]]
) -> None:
    state.installed = True
    state.groups[7] = None  # type: ignore[assignment]
    _process_groups.ending(signal.SIGINT)
    state.groups[8] = None  # type: ignore[assignment]
    assert _process_groups.ending(signal.SIGTERM)
    assert sent == [(7, signal.SIGINT), (8, signal.SIGTERM)]
    assert state.closing is not None and state.closing[0] == signal.SIGINT


def test_a_late_sigterm_kills_every_group_and_ends_untaped(
    state: _process_groups._State, sent: list[tuple[int, int]], died: list[int]
) -> None:
    state.installed = True
    state.groups[7] = None  # type: ignore[assignment]
    state.closing = (signal.SIGINT, time.monotonic() - 10 * GRACE)
    with pytest.raises(SystemExit):
        _process_groups.ending(signal.SIGTERM)
    assert sent == [(7, signal.SIGKILL)]
    assert died == [signal.SIGTERM]


# ── the handler ─────────────────────────────────────────────────────────────


def test_the_handler_raises_interrupted_once_and_ignores_a_repeat(
    state: _process_groups._State, sent: list[tuple[int, int]]
) -> None:
    state.installed = True
    with pytest.raises(Interrupted) as raised:
        _process_groups._on_signal(signal.SIGTERM, None)
    assert raised.value.signum == signal.SIGTERM
    _process_groups._on_signal(signal.SIGHUP, None)  # the same event: no second raise


def test_the_handler_defers_a_signal_while_the_main_thread_starts_a_child(
    state: _process_groups._State,
) -> None:
    state.main_spawning = True
    _process_groups._on_signal(signal.SIGINT, None)
    assert state.deferred == [signal.SIGINT]


def test_an_earlier_handler_decides_and_untaped_does_not_close(
    state: _process_groups._State, sent: list[tuple[int, int]]
) -> None:
    calls: list[int] = []

    def earlier(signum: int, frame: FrameType | None) -> None:
        calls.append(signum)

    state.installed = True
    state.previous[signal.SIGTERM] = earlier
    state.groups[7] = None  # type: ignore[assignment]
    _process_groups._on_signal(signal.SIGTERM, None)
    assert calls == [signal.SIGTERM]
    assert sent == [(7, signal.SIGTERM)]
    assert state.closing is None


def test_dying_restores_the_default_action_and_kills_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(signal, "signal", lambda sig, action: calls.append(("signal", sig)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(("kill", sig)))

    def exit_(code: int) -> None:
        calls.append(("exit", code))
        raise SystemExit(code)

    monkeypatch.setattr(os, "_exit", exit_)
    with pytest.raises(SystemExit):
        _process_groups._die(signal.SIGTERM)
    assert calls == [
        ("signal", signal.SIGTERM),
        ("kill", signal.SIGTERM),
        ("exit", 128 + signal.SIGTERM),
    ]


# ── install ─────────────────────────────────────────────────────────────────


def _fake_handlers(monkeypatch: pytest.MonkeyPatch, handlers: dict[int, Any]) -> None:
    monkeypatch.setattr(signal, "getsignal", handlers.get)
    monkeypatch.setattr(signal, "signal", handlers.__setitem__)


def test_install_handles_the_three_signals_and_leaves_an_ignored_one(
    monkeypatch: pytest.MonkeyPatch, state: _process_groups._State
) -> None:
    handlers: dict[int, Any] = {
        signal.SIGINT: signal.default_int_handler,
        signal.SIGTERM: signal.SIG_IGN,
        signal.SIGHUP: signal.SIG_DFL,
    }
    _fake_handlers(monkeypatch, handlers)
    _process_groups.install()
    _process_groups.install()  # a second call keeps the first one's previous handlers
    assert handlers == {
        signal.SIGINT: _process_groups._on_signal,
        signal.SIGTERM: signal.SIG_IGN,
        signal.SIGHUP: _process_groups._on_signal,
    }
    assert state.previous == {
        signal.SIGINT: signal.default_int_handler,
        signal.SIGHUP: signal.SIG_DFL,
    }
    assert state.installed


def test_install_off_the_main_thread_does_nothing(
    monkeypatch: pytest.MonkeyPatch, state: _process_groups._State
) -> None:
    handlers: dict[int, Any] = {}
    _fake_handlers(monkeypatch, handlers)
    worker = threading.Thread(target=_process_groups.install)
    worker.start()
    worker.join()
    assert not handlers
    assert not state.installed


# ── spawn ───────────────────────────────────────────────────────────────────


def _in_a_worker[T](call: Callable[[], T]) -> T:
    results: list[T] = []
    worker = threading.Thread(target=lambda: results.append(call()))
    worker.start()
    worker.join()
    return results[0]


def test_a_worker_spawn_once_closing_starts_nothing(state: _process_groups._State) -> None:
    state.closing = (signal.SIGTERM, time.monotonic())
    spawned = _in_a_worker(lambda: _process_groups.spawn(["true"], cwd=None, env=None, stdin=0))
    assert spawned is None
    assert not state.spawning


def test_a_worker_child_started_as_untaped_closed_gets_the_signal_too(
    monkeypatch: pytest.MonkeyPatch, state: _process_groups._State
) -> None:
    real = subprocess.Popen

    def popen_then_close(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real(*args, **kwargs)
        state.closing = (0, time.monotonic())  # the exit net closed while Popen ran
        return process

    monkeypatch.setattr(subprocess, "Popen", popen_then_close)
    process = _in_a_worker(_spawn)
    assert _process_groups.clock_age(process.pid) is not None
    assert process.wait(timeout=10) == -signal.SIGTERM
    _finish(process)


def test_a_signal_deferred_during_a_main_thread_spawn_stops_that_child(
    monkeypatch: pytest.MonkeyPatch, state: _process_groups._State
) -> None:
    real = subprocess.Popen
    started: list[subprocess.Popen[bytes]] = []

    def popen_then_signal(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real(*args, **kwargs)
        started.append(process)
        _process_groups._on_signal(signal.SIGINT, None)  # lands mid-spawn: deferred
        return process

    monkeypatch.setattr(subprocess, "Popen", popen_then_signal)
    with pytest.raises(Interrupted):
        _spawn()
    assert started[0].poll() is not None
    assert not state.groups


# ── groups ──────────────────────────────────────────────────────────────────


def test_a_leader_nobody_reaped_is_reaped_with_its_group() -> None:
    process = _spawn([sys.executable, "-c", "pass"])
    deadline = time.monotonic() + 10
    while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    _process_groups._reap_leftovers(process.pid)
    assert _process_groups.emptied(process)
    _process_groups.close_pipes(process)


class _Leader:
    """A leader without process groups: records what stopping it does."""

    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.calls: list[str] = []

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.calls.append("kill")

    def terminate(self) -> None:
        self.calls.append("terminate")


def test_without_groups_only_a_live_leader_is_signalled(
    monkeypatch: pytest.MonkeyPatch, state: _process_groups._State
) -> None:
    monkeypatch.setattr(_process_groups, "GROUPS", False)
    live, ended = _Leader(None), _Leader(0)
    state.groups.update({1: live, 2: ended})  # type: ignore[dict-item]
    assert _process_groups._signal(1, signal.SIGTERM)
    assert _process_groups._signal(1, signal.SIGKILL)
    assert not _process_groups._signal(2, signal.SIGTERM)
    assert not _process_groups._signal(3, signal.SIGTERM)
    assert (live.calls, ended.calls) == (["terminate", "kill"], [])


def test_a_forked_child_starts_with_nothing(state: _process_groups._State) -> None:
    vars(state).update(groups={7: None}, closing=(signal.SIGINT, 0.0), main_spawning=True)
    _process_groups._after_fork_in_child()
    assert not state.groups
    assert (state.closing, state.main_spawning) == (None, False)


# ── the exit net ────────────────────────────────────────────────────────────


def test_the_exit_net_stops_what_is_left_and_closes(state: _process_groups._State) -> None:
    state.installed = True
    process = _spawn()
    state.spawning[0] = None  # a worker between its mark and Popen's end
    threading.Timer(0.05, state.spawning.clear).start()
    _process_groups._exit_net()
    assert process.poll() == -signal.SIGTERM
    assert not state.groups
    assert state.closing is not None and state.closing[0] == 0
    _process_groups.close_pipes(process)


def test_the_exit_net_kills_a_child_that_ignores_sigterm(state: _process_groups._State) -> None:
    code = "import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    code += "print(flush=True); time.sleep(30)"
    process = _spawn([sys.executable, "-c", code])
    assert process.stdout is not None
    process.stdout.readline()  # it ignores SIGTERM from here on
    _process_groups._exit_net()
    assert process.poll() == -signal.SIGKILL
    _process_groups.close_pipes(process)


def test_the_exit_net_ends_untaped_by_the_sigterm_that_closed_it(
    monkeypatch: pytest.MonkeyPatch, state: _process_groups._State, died: list[int]
) -> None:
    shutdowns: list[bool] = []
    monkeypatch.setattr(logging, "shutdown", lambda: shutdowns.append(True))
    state.installed = True
    state.closing = (signal.SIGTERM, time.monotonic())
    with pytest.raises(SystemExit):
        _process_groups._exit_net()
    assert (shutdowns, died) == ([True], [signal.SIGTERM])
