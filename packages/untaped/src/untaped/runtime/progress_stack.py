"""Progress on stderr: the task stack, its one ticker, the spinner and the line cadence.

Open ``progress(label)`` blocks are one process-wide task stack, ordered by
open time: the first task's line is the one shown, and the last one opened
owns its phase (a nested task is a phase of the outer line). One ticker
thread runs while the stack is not empty: it redraws the spinner on a
terminal and, elsewhere, prints the late start line and the heartbeat.
Every progress line is an ``untaped.progress`` event at one cadence
(:func:`_due`), read from a clock ``untaped.sdk.testing`` can freeze, and
goes out through the task's sink (:class:`ProgressSink`).

A progress line is the one line built and written under the write lock
(:mod:`untaped.runtime.stderr`), so lines print in the order the task's
state changed (a ``done`` line never precedes a ``running`` one). Every
other line is written around the spinner (:func:`write`). While a screen
holds the terminal (:func:`hold_screen`) no spinner is drawn and running
text lines are dropped, not held.
"""

from __future__ import annotations

import shutil
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Literal, Protocol, TextIO

from untaped.runtime.stderr import inside, locked, raw
from untaped.sdk._run import OutputPolicy, run_clock
from untaped.sdk.diagnostic import Progress
from untaped.sdk.progress import ProgressHandle


class ProgressSink(Protocol):
    """Where a task's lines go: its run's sink."""

    def emit_progress(
        self, progress: Progress, policy: OutputPolicy, *, droppable: bool = True
    ) -> None:
        """Print one progress line per ``policy`` (``droppable``: dropped under a screen)."""
        ...


# --------------------------------------------------------------------------- #
# Around the spinner: every line clears it and redraws it
# --------------------------------------------------------------------------- #

#: The width of the spinner line on screen (0: none drawn). Guarded by the lock.
_drawn = 0
#: How many screens hold the terminal, across every open run. Guarded by the lock.
_holds = 0


def write(text: str) -> None:
    """Write ``text`` to stderr in one ``write()``, around the spinner, under the lock.

    A write from a thread already inside the lock (a re-entrant call) goes
    straight to the stream instead of blocking on itself.
    """
    if inside():
        raw(text)
        return
    with locked():
        raw(framed(text))


def framed(text: str) -> str:
    """``text`` preceded by clearing the spinner line and followed by redrawing it.

    Nothing is drawn while a screen holds the terminal. Called under the lock.
    """
    global _drawn
    out = f"\r{' ' * _drawn}\r" if _drawn else ""
    _drawn = 0
    out += text
    line = _spinner_line()
    if line is not None:
        out += f"\r{line}"
        _drawn = len(line)
    return out


def hold_screen() -> None:
    """Count one more screen holding the terminal; it starts on a clean line."""
    global _drawn, _holds
    with locked():
        _holds += 1
        if _drawn:
            raw(f"\r{' ' * _drawn}\r")
            _drawn = 0


def release_screen() -> None:
    """Count one screen fewer holding the terminal."""
    global _holds
    with locked():
        _holds -= 1


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_ASCII_SPINNER_FRAMES = "|/-\\"


def _spinner_frames(stream: TextIO) -> str:
    """Braille frames on a UTF-8 stream, ASCII frames otherwise.

    An unknown encoding (e.g. a ``StringIO`` with no ``encoding`` attribute) is
    assumed UTF-8; only a stream that *declares* a non-UTF encoding (a legacy
    ``LANG=C`` terminal) falls back to ASCII so the spinner doesn't render as
    replacement boxes.
    """
    encoding = (getattr(stream, "encoding", None) or "").lower()
    if encoding and "utf" not in encoding:
        return _ASCII_SPINNER_FRAMES
    return _SPINNER_FRAMES


def _fit(line: str) -> str:
    """Truncate a spinner line to the terminal width so ``\\r`` repaints in place.

    A line wider than the terminal wraps, and ``\\r`` then only returns to the
    start of the *last* row, leaving stale fragments behind.
    """
    width = shutil.get_terminal_size(fallback=(80, 24)).columns - 1
    if width <= 1 or len(line) <= width:
        return line
    return line[: width - 1] + "…"


# --------------------------------------------------------------------------- #
# Progress: the task stack, its one ticker and the line cadence
# --------------------------------------------------------------------------- #

#: Nothing on a line renderer for a task's first seconds.
_QUIET_PERIOD = 2.0
#: A line on each step of this many percent of ``total``.
_STEP_PERCENT = 10
#: A line when this many seconds pass without one.
_HEARTBEAT = 30.0
#: How often the ticker redraws the spinner and checks the deadlines.
_TICK = 0.1

type Mode = Literal["spinner", "lines", "silent"]

#: Open tasks, by open time: the first is the line's, the last owns its phase. Guarded by the lock.
_tasks: list[_Task] = []
#: What progress time a test holds tasks to (:func:`cadence_clock`), else the run's clock.
_cadence: list[Callable[[], float] | None] = [None]


@contextmanager
def cadence_clock(clock: Callable[[], float]) -> Iterator[None]:
    """Time the progress cadence of tasks opened in the block by ``clock`` (a test's).

    Only the cadence reads it; every other reader of the run's clock (a
    deadline, a back-off) keeps its time.
    """
    previous, _cadence[0] = _cadence[0], clock
    try:
        yield
    finally:
        _cadence[0] = previous


def frozen_cadence() -> AbstractContextManager[None]:
    """Hold progress time still in the block: no line falls due.

    ``--verbose`` still prints every update.
    """
    return cadence_clock(lambda: 0.0)


def cadence_is_set() -> bool:
    """Whether a test holds the progress cadence to its own clock now."""
    return _cadence[0] is not None


#: The thread driving the spinner and the deadlines while a task is open. Guarded by the lock.
_ticker: _Ticker | None = None
#: The spinner's frame counter. Guarded by the lock.
_frame = 0


class _Task:
    """One open ``progress(label)`` block: its handle, its state and its line's cadence.

    Every field is guarded by the write lock. The cadence fields (``started``,
    ``last_line``, ``shown_phase``, ``shown_step``) matter while the task is
    the first of the stack, the one whose line is printed.
    """

    def __init__(
        self,
        label: str,
        *,
        sink: ProgressSink,
        policy: OutputPolicy,
        mode: Mode,
        verbose: bool,
        frames: str,
    ) -> None:
        self.label = label
        self.sink = sink
        #: The policy at open: the sink's lines follow it for the task's life.
        self.policy = policy
        self.mode = mode
        self.verbose = verbose
        self.frames = frames
        #: The clock at open, so a task outliving a test's clock keeps its own.
        self.clock = _cadence[0] or run_clock().monotonic
        self.opened = self.clock()
        self.phase: str | None = None
        self.done: int | None = None
        self.total: int | None = None
        self.message: str | None = None
        #: An update from a task between the first and the last, shown as the line's text.
        self.aside: str | None = None
        self.started = False
        self.last_line = self.opened
        self.shown_phase: str | None = None
        self.shown_step: int | None = None

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        with locked():
            if phase is not None and phase != self.phase:
                self.phase, self.done, self.total = phase, None, None
            if done is not None:
                self.done = done
            if total is not None:
                self.total = total
            self.message = message
            if self not in _tasks:
                return
            first = _tasks[0]
            first.aside = None if self in (first, _tasks[-1]) else _aside(self)
            _print(_due(first, "update"))
            if first.mode == "spinner":
                raw(framed(""))


def _aside(task: _Task) -> str:
    """A concurrent inner task's update, as the line's text: ``<label>[ <counts>][ · <text>]``."""
    text = task.label + _counts(task.done, task.total)
    return text if not task.message else f"{text} · {task.message}"


def _counts(done: int | None, total: int | None) -> str:
    if done is None:
        return ""
    return f" {done}" if total is None else f" {done}/{total}"


class _View:
    """What the first task's line shows: the innermost task's phase and counts."""

    def __init__(self, first: _Task) -> None:
        last = _tasks[-1] if _tasks and first in _tasks else first
        self.label = first.label
        if last is first:
            self.phase = first.phase
        else:
            self.phase = last.label if last.phase is None else f"{last.label} · {last.phase}"
        self.done, self.total = last.done, last.total
        self.text = first.aside or last.message

    def step(self) -> int | None:
        """The ``_STEP_PERCENT`` step the counts reached, or ``None`` without a ``total``."""
        if self.done is None or not self.total:
            return None
        return min(max(self.done, 0), self.total) * 100 // self.total // _STEP_PERCENT


def _compose(
    label: str,
    *,
    phase: str | None,
    done: int | None,
    total: int | None,
    text: str | None,
    elapsed: float,
    state: str = "running",
) -> str:
    """``<label>[ · <phase>][ <done>/<total>][ · <text>] (<N>s)``, or ``<label>: done …``."""
    tail = "" if not text else f" · {text}"
    if state != "running":
        return f"{label}: {state}{tail} ({_seconds(elapsed)}s)"
    head = label if phase is None else f"{label} · {phase}"
    return f"{head}{_counts(done, total)}{tail} ({_seconds(elapsed)}s)"


def _seconds(elapsed: float) -> int:
    """Whole seconds, rounded half up: 46.8 shows as 47."""
    return int(elapsed + 0.5)


#: One line to print, built and printed under the lock: the task and the event.
type _Line = tuple[_Task, Progress] | None


def _snapshot(task: _Task, view: _View, now: float, state: str = "running") -> _Line:
    """The line ``task`` prints now; records it as the task's last line. Called under the lock."""
    task.last_line = now
    elapsed = now - task.opened
    running = state == "running"
    progress = Progress(
        message=_compose(
            view.label,
            phase=view.phase,
            done=view.done,
            total=view.total,
            text=view.text if running else task.message,
            elapsed=elapsed,
            state=state,
        ),
        hint=None,
        label=view.label,
        phase=view.phase if running else None,
        done=view.done if running else None,
        total=view.total if running else None,
        elapsed=round(elapsed, 1),
        state=state,
    )
    return task, progress


type _Why = Literal["open", "update", "tick"]


def _due(first: _Task, why: _Why) -> _Line:
    """The line ``first``'s cadence prints now, if any: start, phase, step or heartbeat.

    Called under the lock, at an open, after an update, or from a tick.
    Under verbose an open or an update always prints and a tick prints only
    the heartbeat. A phase or step is recorded as shown even when nothing
    prints (inside the quiet period, or under a screen), so the start line
    carries the current state and no older step prints after it.
    """
    if first.mode != "lines":
        return None
    now = first.clock()
    view = _View(first)
    step = view.step()
    phase_changed = view.phase != first.shown_phase
    stepped = step is not None and (first.shown_step is None or step > first.shown_step)
    if phase_changed:
        first.shown_phase, first.shown_step = view.phase, step
    elif stepped:
        first.shown_step = step
    if _holds and first.policy.diagnostics == "text":
        return None  # dropped, not queued: a screen owns the terminal
    heartbeat = now - first.last_line >= _HEARTBEAT
    if first.verbose:
        if why == "tick" and not heartbeat:
            return None
        first.started = True
        return _snapshot(first, view, now)
    if not first.started:
        if now - first.opened < _QUIET_PERIOD:
            return None
        first.started = True
        return _snapshot(first, view, now)
    if phase_changed or stepped or heartbeat:
        return _snapshot(first, view, now)
    return None


def _print(line: _Line, *, ending: bool = False) -> None:
    """Print a progress line under the lock that built it (``ending``: held, never dropped)."""
    if line is not None:
        task, progress = line
        task.sink.emit_progress(progress, task.policy, droppable=not ending)


def _spinner_line() -> str | None:
    """The spinner frame for the first task, or ``None``. Called under the lock."""
    global _frame
    if not _tasks or _holds or _tasks[0].mode != "spinner":
        return None
    first = _tasks[0]
    view = _View(first)
    glyph = first.frames[_frame % len(first.frames)]
    _frame += 1
    line = _compose(
        view.label,
        phase=view.phase,
        done=view.done,
        total=view.total,
        text=view.text,
        elapsed=first.clock() - first.opened,
    )
    return _fit(f"{glyph} {line}")


class _Ticker:
    """The one thread that redraws the spinner and fires the late start and the heartbeat."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="untaped-progress", daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(_TICK):
            try:
                tick()
            except Exception:  # a failed write must not stop the spinner for good
                continue


def tick() -> None:
    """Redraw the spinner, or print the first task's late start line or heartbeat, if due."""
    with locked():
        if not _tasks:
            return
        first = _tasks[0]
        if first.mode == "spinner":
            if not _holds:
                raw(framed(""))
            return
        _print(_due(first, "tick"))


@contextmanager
def open_task(
    label: str, *, sink: ProgressSink, policy: OutputPolicy, mode: Mode, verbose: bool
) -> Iterator[ProgressHandle]:
    """Open a task on the stack for the block; close it ``failed`` when the block raises.

    The task is closed whatever raises once it is on the stack, its open
    line's write and its ticker's start included, so no task outlives it.
    """
    global _ticker
    task = _Task(
        label,
        sink=sink,
        policy=policy,
        mode=mode,
        verbose=verbose,
        frames=_spinner_frames(sys.stderr),
    )
    state = "done"
    try:
        starting: _Ticker | None = None
        with locked():
            _tasks.append(task)
            if _ticker is None:
                _ticker = starting = _Ticker()
            first = _tasks[0]
            if first.verbose or first is task:
                _print(_due(first, "open"))
            if first.mode == "spinner":
                raw(framed(""))
        if starting is not None:
            starting.thread.start()
        yield task
    except BaseException:
        state = "failed"
        raise
    finally:
        _close(task, state)


def _close(task: _Task, state: str) -> None:
    """Remove ``task`` wherever it sits, print its end line, join an idle ticker."""
    global _ticker
    stopping: _Ticker | None = None
    with locked():
        first = _tasks[0] if _tasks else None
        if task in _tasks:
            _tasks.remove(task)
        if not _tasks and _ticker is not None:
            stopping, _ticker = _ticker, None
            stopping.stop.set()
        if task is first and task.mode == "lines" and task.started:
            # Held, not dropped, under a screen: one end line for each start line.
            _print(_snapshot(task, _View(task), task.clock(), state), ending=True)
        elif _tasks:
            # The phase reverts to the next open task's: shown as is, not as a new phase.
            head = _tasks[0]
            head.aside = None
            view = _View(head)
            head.shown_phase, head.shown_step = view.phase, view.step()
        if task.mode == "spinner" or _drawn:
            raw(framed(""))
    thread = stopping.thread if stopping is not None else None
    # never under the lock: the ticker takes it; a ticker that never started has nothing to join
    if thread is not None and thread.ident is not None and thread is not threading.current_thread():
        thread.join()


def open_tasks() -> int:
    """How many progress tasks are open in the process (a test's leak check)."""
    with locked():
        return len(_tasks)


def drop_tasks() -> int:
    """Empty the task stack and stop its ticker; how many tasks were open (a test's leak cleanup).

    A leaked task would otherwise stay first on the stack and own every
    later test's line.
    """
    global _ticker
    with locked():
        leaked = len(_tasks)
        _tasks.clear()
        stopping, _ticker = _ticker, None
        if stopping is not None:
            stopping.stop.set()
    if stopping is not None and stopping.thread.ident is not None:
        stopping.thread.join()
    return leaked
