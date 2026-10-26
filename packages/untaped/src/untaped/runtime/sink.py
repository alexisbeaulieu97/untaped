"""The one writer to stderr: the invocation's scope, its sink, and progress.

:func:`diagnostics_scope` scopes one invocation: its :class:`OutputPolicy`
(inherited from an enclosing scope, so a nested scope starts with what argv
asked for), its sink, and the most severe failure it counted. Every stderr
line is a :class:`~untaped.sdk.diagnostic.Diagnostic` emitted through
:func:`current_sink`, which renders it as text or as one JSON line (``level``
and ``kind`` first) per the policy, and writes it in one ``write()`` under
one process-wide lock, clearing and redrawing the spinner around it.

Rules the sink keeps:

- **Counting.** Emitting a :class:`~untaped.sdk.diagnostic.Failure` (a
  subclass too) counts its ``exit_code`` toward the scope's exit code
  (:func:`failure_exit_code`); a ``StaleData`` whose ``error`` is set is a
  warning and counts nothing.
- **Muting.** ``--quiet`` mutes ``info``, ``success`` and progress, never a
  warning, a failure or a hint; ``debug`` prints only under ``--verbose``.
- **Never dropped.** Off its scope a line reaches the most recently entered
  live scope (``untaped.runtime.output_policy`` says how, and its one documented
  limit); with no scope at all it goes to a default text sink. ``emit`` on a
  closed sink writes the line plainly. The stream is ``sys.stderr`` read at
  each write, so ``capsys`` and ``redirect_stderr`` see it.
- **No deadlock.** The lock is never held across a callback or a thread
  ``join()``; nothing in this package logs; and a nested write from a thread
  that already holds the lock writes without taking it. A progress line is
  the one line built and written under the lock, so lines print in the
  order the task's state changed (a ``done`` line never precedes a
  ``running`` one).
- **Screens.** While a screen owns the terminal (:meth:`Sink.hold`), text
  lines are held and printed after it exits; JSON lines are not held.
  Progress is the exception: no spinner is drawn and running text progress
  lines are dropped, not held, while any live scope holds a screen; a task's
  ``done`` or ``failed`` line is held like any other when its start line
  printed (held by its own scope's sink: a screen another scope holds does
  not hold it, as for any other line).
- **Progress.** Open ``progress(label)`` blocks are one process-wide task
  stack, ordered by open time: the first task's line is the one shown, and
  the last one opened owns its phase (a nested task is a phase of the outer
  line). One ticker thread runs while the stack is not empty: it redraws
  the spinner on a terminal and, elsewhere, prints the late start line and
  the heartbeat. Every progress line is an ``untaped.progress`` event at one
  cadence (:func:`_due`), read from a clock ``untaped.sdk.testing`` can freeze.

A presentation (``rendered``) may accompany a diagnostic: the exact text the
text renderer prints (a callable taking whether to colour, or a string). It
is not on the wire and never reaches a JSON line: a themed ``ui.message``
line, a styled ``info`` line, an error with its ``hint:`` line, a debug log
record as its formatter writes it.
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, Protocol, TextIO

from pydantic import BaseModel

from untaped.runtime.output_policy import (
    OutputPolicy,
    context_holder,
    current_holder,
    current_policy,
    enter,
    leave,
    unscoped_policy,
)
from untaped.runtime.redaction import redact_url_password
from untaped.runtime.render import (
    MessageKind,
    render_message,
    render_styled,
    should_colorize,
    stream_is_tty,
)
from untaped.runtime.theme import BUILTIN_THEMES
from untaped.sdk._run import recorder, run_clock
from untaped.sdk.diagnostic import (
    NOT_COUNTED,
    Debug,
    DeprecationNotice,
    Diagnostic,
    Failure,
    Message,
    Progress,
    StaleData,
    format_error,
    kind_of,
    split_hint,
)
from untaped.sdk.errors import ErrorCategory, ExitCode, UntapedError, combine_exit_codes
from untaped.sdk.progress import ProgressHandle
from untaped.sdk.records import OutcomeRecord

if TYPE_CHECKING:
    from rich.text import Text

    from untaped.runtime.theme import ThemeSpec

type Rendered = Callable[[bool], str] | str
"""A line's text presentation: a string, or a callable given whether to colour."""

_HINT_PREFIX = "hint: "
_ECHO_LEVELS = (("warning: ", "warning"), (_HINT_PREFIX, "hint"))

# --------------------------------------------------------------------------- #
# The writer: one lock, one write per line, the spinner cleared and redrawn
# --------------------------------------------------------------------------- #

_WRITE_LOCK = threading.Lock()
_thread = threading.local()
#: The width of the spinner line on screen (0: none drawn). Guarded by the lock.
_drawn = 0
#: How many screens hold the terminal, across every live scope. Guarded by the lock.
_holds = 0


@contextmanager
def _locked() -> Iterator[None]:
    """Hold the write lock, marking this thread inside it; a no-op for a thread already inside.

    A write made while it is held (a stream that logs, say) goes straight to
    the stream instead of blocking on the lock this thread holds.
    """
    if getattr(_thread, "inside", False):
        yield
        return
    with _WRITE_LOCK:
        _thread.inside = True
        try:
            yield
        finally:
            _thread.inside = False


def _write(text: str) -> None:
    """Write ``text`` to stderr in one ``write()``, around the spinner, under the lock.

    A write from a thread already inside the lock (a re-entrant call) goes
    straight to the stream instead of blocking on itself.
    """
    if getattr(_thread, "inside", False):
        _raw(text)
        return
    with _locked():
        _raw(_framed(text))


def _raw(text: str) -> None:
    stream = sys.stderr
    stream.write(text)
    stream.flush()


def _framed(text: str) -> str:
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

type _Mode = Literal["spinner", "lines", "silent"]

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
        sink: _ScopeSink,
        policy: OutputPolicy,
        mode: _Mode,
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
        with _locked():
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
                _raw(_framed(""))


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
    with _locked():
        if not _tasks:
            return
        first = _tasks[0]
        if first.mode == "spinner":
            if not _holds:
                _raw(_framed(""))
            return
        _print(_due(first, "tick"))


@contextmanager
def _task(
    label: str, *, sink: _ScopeSink, policy: OutputPolicy, mode: _Mode, verbose: bool
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
        with _locked():
            _tasks.append(task)
            if _ticker is None:
                _ticker = starting = _Ticker()
            first = _tasks[0]
            if first.verbose or first is task:
                _print(_due(first, "open"))
            if first.mode == "spinner":
                _raw(_framed(""))
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
    with _locked():
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
            _raw(_framed(""))
    thread = stopping.thread if stopping is not None else None
    # never under the lock: the ticker takes it; a ticker that never started has nothing to join
    if thread is not None and thread.ident is not None and thread is not threading.current_thread():
        thread.join()


def open_tasks() -> int:
    """How many progress tasks are open in the process (a test's leak check)."""
    with _locked():
        return len(_tasks)


def drop_tasks() -> int:
    """Empty the task stack and stop its ticker; how many tasks were open (a test's leak cleanup).

    A leaked task would otherwise stay first on the stack and own every
    later test's line.
    """
    global _ticker
    with _locked():
        leaked = len(_tasks)
        _tasks.clear()
        stopping, _ticker = _ticker, None
        if stopping is not None:
            stopping.stop.set()
    if stopping is not None and stopping.thread.ident is not None:
        stopping.thread.join()
    return leaked


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _level(diagnostic: Diagnostic) -> str:
    return str(getattr(diagnostic, "level", "info"))


def _text(diagnostic: Diagnostic, *, theme: ThemeSpec | None, color: bool) -> str:
    """The text line of a diagnostic emitted without a presentation.

    An ``info``, ``success`` or ``warning`` message and a deprecation render
    through the theme (``theme``: the policy's, else the default), as
    ``ui.message`` does; its ``hint:`` line, a failure, a debug line and a
    stale-data line do not.
    """
    level = _level(diagnostic)
    if isinstance(diagnostic, Debug):
        return f"DEBUG {diagnostic.logger}: {diagnostic.message}"
    message = diagnostic.message
    themed = _themed_level(diagnostic)
    if themed is not None:
        line = render_message(
            themed,
            message,
            theme=theme or BUILTIN_THEMES["default"],
            colorize=color,
        )
        return line if diagnostic.hint is None else f"{line}\n{_HINT_PREFIX}{diagnostic.hint}"
    if isinstance(diagnostic, Failure) and diagnostic.item is not None:
        message = f"{diagnostic.item}: {message}"
    line = f"{level}: {message}" if level in ("error", "warning", "hint") else message
    if diagnostic.hint is not None:
        line += f"\n{_HINT_PREFIX}{diagnostic.hint}"
    return line


def _themed_level(diagnostic: Diagnostic) -> MessageKind | None:
    """The theme level ``diagnostic`` renders through, if it renders through the theme."""
    if isinstance(diagnostic, DeprecationNotice):
        return "warning"
    if isinstance(diagnostic, Message) and diagnostic.level != "hint":
        return diagnostic.level
    return None


def _blank(diagnostic: Diagnostic) -> bool:
    """Whether ``diagnostic`` is a blank ``info`` line: a separator, not a message."""
    return (
        isinstance(diagnostic, Message)
        and diagnostic.level == "info"
        and not diagnostic.message.strip()
        and diagnostic.hint is None
    )


def _json(diagnostic: Diagnostic) -> str:
    """One JSON line: ``level`` and ``kind``, then the diagnostic's fields.

    A message's ``message`` has its URL passwords masked.
    """
    record = {"level": _level(diagnostic), "kind": kind_of(type(diagnostic))}
    fields = diagnostic.model_dump(mode="json")
    if isinstance(diagnostic, Message):
        fields["message"] = redact_url_password(diagnostic.message)
    return json.dumps(record | fields, default=str)


def echo_diagnostic(text: str) -> Diagnostic | None:
    """The diagnostic an ``echo(text, err=True)`` line stands for (``None``: a blank one).

    An ``error:`` prefix is a :meth:`~untaped.sdk.diagnostic.Failure.plain`
    (its ``hint:`` lines become ``hint``); ``warning:`` and ``hint:`` are a
    :class:`~untaped.sdk.diagnostic.Message` of that level; anything else
    is ``info``. URL passwords are masked.
    """
    if not text.strip():
        return None
    text = redact_url_password(text)
    if text.startswith("error: "):
        message, hint = split_hint(text.removeprefix("error: "))
        return Failure.plain(message, hint=hint)
    for prefix, level in _ECHO_LEVELS:
        if text.startswith(prefix):
            return Message(level=level, message=text.removeprefix(prefix).strip())
    return Message(level="info", message=text.strip())


# --------------------------------------------------------------------------- #
# The scope and its sink
# --------------------------------------------------------------------------- #


class Sink(Protocol):
    """Where an invocation's stderr lines go."""

    def emit(
        self, diagnostic: Diagnostic, *, rendered: Rendered | None = None, unmuted: bool = False
    ) -> None:
        """Count, render and write one line (``unmuted``: ``--quiet`` never mutes it)."""
        ...

    def echo(self, text: str, *, end: str = "\n") -> None:
        """Print ``text`` verbatim, or its diagnostic as a JSON line (:func:`echo_diagnostic`)."""
        ...

    def relay(self, text: str, *, end: str = "\n") -> None:
        """Print another tool's output line as is: never parsed, never counted, never muted.

        Under JSON diagnostics it is an ``info`` line, whatever prefix it has
        (a hook that prints ``error: …`` and succeeds is not a failure).
        """
        ...

    def passthrough(self, text: str) -> None:
        """Write ``text`` verbatim (a relayed child's output), never parsed or counted."""
        ...

    def progress(
        self, label: str, *, verbose: bool = False, quiet: bool = False
    ) -> AbstractContextManager[ProgressHandle]:
        """Open a task for a blocking operation (``verbose``/``quiet`` add to the policy's).

        A spinner on a terminal; elsewhere ``untaped.progress`` lines, text or
        JSON, at the line cadence; inside another task, a phase of its line.
        """
        ...

    def hold(self) -> AbstractContextManager[None]:
        """Hold text lines while a screen owns the terminal; print them after."""
        ...

    def close(self) -> None:
        """Print any held lines; later lines are written plainly. Idempotent."""
        ...


class _Scope:
    """One invocation: its policy, its sink and the most severe failure it counted."""

    def __init__(self, policy: OutputPolicy) -> None:
        self.policy = policy
        self._lock = threading.Lock()
        self._worst: int = ExitCode.OK
        self.sink = _ScopeSink(self)

    def note(self, code: int) -> None:
        with self._lock:
            self._worst = combine_exit_codes(self._worst, code)

    @property
    def worst(self) -> int:
        with self._lock:
            return self._worst


class _ScopeSink:
    """The sink of one scope (or, with no scope, of the default ``_Unscoped`` policy)."""

    def __init__(self, scope: _Scope | None) -> None:
        self._scope = scope
        #: The test's recorder when the scope started (``Install.running()``'s ``diagnostics``).
        self._recorder = recorder() if scope is not None else None
        self._held: list[str] = []
        self._holding = 0
        self._closed = False
        self._stale: set[tuple[str, str]] = set()
        self._state_lock = threading.Lock()

    @property
    def policy(self) -> OutputPolicy:
        return self._scope.policy if self._scope is not None else current_policy()

    def emit(
        self, diagnostic: Diagnostic, *, rendered: Rendered | None = None, unmuted: bool = False
    ) -> None:
        self._emit(diagnostic, rendered, unmuted=unmuted, end="\n")

    def echo(self, text: str, *, end: str = "\n") -> None:
        diagnostic = echo_diagnostic(text)
        if diagnostic is not None:
            self._emit(diagnostic, text, unmuted=True, end=end)
        elif self.policy.diagnostics == "text":
            self._out(text + end)

    def _emit(
        self,
        diagnostic: Diagnostic,
        rendered: Rendered | None,
        *,
        unmuted: bool,
        end: str,
        droppable: bool = False,
        policy: OutputPolicy | None = None,
    ) -> None:
        """Count, mute, record and write one line.

        A blank ``info`` (no text, no hint) is a text separator: an empty
        line in text, nothing under JSON, muted like any ``info``.
        """
        if kind_of(type(diagnostic)) is None:
            raise TypeError(
                f"{type(diagnostic).__qualname__} declares no kind, so it is not a line: "
                "emit a subclass declared with kind=…"
            )
        if isinstance(diagnostic, StaleData) and not self._first_stale(diagnostic):
            return
        if isinstance(diagnostic, Failure) and self._scope is not None:
            self._scope.note(diagnostic.exit_code)
        policy = policy if policy is not None else self.policy
        level = _level(diagnostic)
        if isinstance(diagnostic, Debug) and not policy.verbose:
            return
        if policy.quiet and not unmuted and level in ("info", "success", "progress"):
            return
        if _blank(diagnostic) and rendered is None:
            if policy.diagnostics == "text":
                self._out(end, droppable=droppable)
            return
        if self._recorder is not None:  # what was shown, as the stderr tee shows it
            self._recorder.append(diagnostic)
        if policy.diagnostics == "json":
            _write(_json(diagnostic) + "\n")
            return
        color = should_colorize(sys.stderr)
        if callable(rendered):
            text = rendered(color)
        elif rendered is not None:
            text = rendered
        else:
            text = _text(diagnostic, theme=policy.theme, color=color)
        self._out(text + end, droppable=droppable)

    def relay(self, text: str, *, end: str = "\n") -> None:
        if not text.strip():
            if self.policy.diagnostics == "text":
                self._out(text + end)
            return
        message = Message(level="info", message=redact_url_password(text).strip())
        self._emit(message, text, unmuted=True, end=end)

    def passthrough(self, text: str) -> None:
        self._out(text)

    def progress(
        self, label: str, *, verbose: bool = False, quiet: bool = False
    ) -> AbstractContextManager[ProgressHandle]:
        policy = self.policy
        verbose = verbose or policy.verbose
        mode: _Mode = "lines"
        if quiet or policy.quiet:
            mode = "silent"
        elif policy.diagnostics == "text" and not verbose and stream_is_tty(sys.stderr):
            mode = "spinner"
        return _task(label, sink=self, policy=policy, mode=mode, verbose=verbose)

    def emit_progress(
        self, progress: Progress, policy: OutputPolicy, *, droppable: bool = True
    ) -> None:
        """Print one progress line per ``policy`` (the task's), muted by ``--quiet``.

        Under a screen a ``droppable`` line is dropped and any other is held.
        """
        self._emit(progress, None, unmuted=False, end="\n", droppable=droppable, policy=policy)

    @contextmanager
    def hold(self) -> Iterator[None]:
        global _drawn, _holds
        with self._state_lock:
            self._holding += 1
        with _locked():
            _holds += 1
            if _drawn:  # the screen starts on a clean line
                _raw(f"\r{' ' * _drawn}\r")
                _drawn = 0
        try:
            yield
        finally:
            with _locked():
                _holds -= 1
            with self._state_lock:
                self._holding -= 1
                held = self._held if self._holding == 0 else []
                if self._holding == 0:
                    self._held = []
            for text in held:
                _write(text)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            held, self._held, self._holding = self._held, [], 0
        for text in held:
            _write(text)

    def _out(self, text: str, *, droppable: bool = False) -> None:
        """Write text, or hold it while a screen owns the terminal (``droppable``: drop it)."""
        with self._state_lock:
            if self._holding and not self._closed:
                if not droppable:
                    self._held.append(text)
                return
        _write(text)

    def _first_stale(self, stale: StaleData) -> bool:
        """Whether this is the scope's first line about ``stale``'s cache entry."""
        key = (stale.cache, stale.key)
        with self._state_lock:
            if key in self._stale:
                return False
            self._stale.add(key)
            return True


_DEFAULT = _ScopeSink(None)


def _scope() -> _Scope | None:
    holder = current_holder()
    return holder if isinstance(holder, _Scope) else None


def current_sink() -> Sink:
    """The current scope's sink, else the default text sink; never ``None``."""
    scope = _scope()
    return scope.sink if scope is not None else _DEFAULT


@contextmanager
def diagnostics_scope() -> Iterator[None]:
    """Scope one invocation: its policy (an enclosing scope's, else the unscoped one) and sink.

    For its duration a Python warning that is shown (not one a filter turns
    into an error or ignores) reaches the sink like any line: an
    :class:`~untaped.sdk.marks.UntapedDeprecationWarning` as the
    ``untaped.deprecation`` record it carries, any other as a warning
    message, so JSON diagnostics stay one object per line. A warning
    something else records (``warnings.catch_warnings(record=True)``,
    ``pytest.warns``) is left to it.
    """
    parent = context_holder()
    policy = replace(parent.policy) if parent is not None else unscoped_policy()
    scope = _Scope(policy)
    token = enter(scope)
    shown = warnings.showwarning
    warnings.showwarning = _WarningRoute(shown)
    try:
        yield
    finally:
        warnings.showwarning = shown
        leave(scope, token)
        scope.sink.close()


#: How Python shows a warning when nothing records it; ``catch_warnings(record=True)``
#: swaps it for its log while it records.
_SHOW_UNRECORDED = getattr(warnings, "_showwarnmsg_impl", None)


def _recording() -> bool:
    """Whether a ``catch_warnings(record=True)`` is recording the warnings shown now.

    It swaps the show function, or with context-aware warnings (``-X
    context_aware_warnings``, the default on free-threaded builds) sets the
    current context's ``log``.
    """
    if getattr(warnings, "_showwarnmsg_impl", None) is not _SHOW_UNRECORDED:
        return True
    context = getattr(warnings, "_get_context", None)
    return context is not None and getattr(context(), "log", None) is not None


class _WarningRoute:
    """``warnings.showwarning`` inside a scope: a warning nothing records goes to the sink."""

    def __init__(self, shown: Callable[..., None]) -> None:
        self._shown = shown

    def __call__(
        self,
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: TextIO | None = None,
        line: str | None = None,
    ) -> None:
        if _recording():
            self._shown(message, category, filename, lineno, file, line)
            return
        _show_warning(message, category, filename, lineno)


def _show_warning(
    message: Warning | str, category: type[Warning], filename: str, lineno: int
) -> None:
    """A shown Python warning as a diagnostic on the sink."""
    from untaped.sdk.marks import UntapedDeprecationWarning  # noqa: PLC0415 - avoids a cycle

    if isinstance(message, UntapedDeprecationWarning):
        current_sink().emit(
            DeprecationNotice(
                message=str(message),
                id=message.id,
                removed_in=message.removed_in,
                distribution=message.distribution,
                replacement=message.replacement,
            )
        )
        return
    text = f"{category.__name__}: {message} ({filename}:{lineno})"
    current_sink().emit(Message(level="warning", message=text))


def count_row_failures(rows: Iterable[object]) -> None:
    """Count each failed row toward the invocation's exit code, as emitting it does.

    A model row counts every top-level field holding a
    :class:`~untaped.sdk.diagnostic.Failure` (its ``error``, or a suite's
    ``failure``) except one marked
    :data:`~untaped.sdk.diagnostic.NOT_COUNTED` (a remembered failure); a
    mapping row counts its ``error`` mapping by its ``exit_code`` or
    ``category``. A row marked failed with no error is not counted.
    """
    scope = _scope()
    if scope is None:
        return
    for row in rows:
        if isinstance(row, BaseModel):
            for name, info in type(row).model_fields.items():
                if NOT_COUNTED in info.metadata:
                    continue
                value = getattr(row, name, None)
                if isinstance(value, Failure):
                    scope.note(value.exit_code)
        elif isinstance(row, Mapping):
            code = _row_error_code(row.get("error"))
            if code is not None:
                scope.note(code)


def count_failed_actions(rows: Iterable[object]) -> None:
    """Count each outcome row whose ``action`` says it failed, error or not: the 12.0 rule.

    ``failed``, ``partial`` and ``conflict`` count ``1`` (a row's error
    counts on its own, through :func:`count_row_failures`), unless a field
    marked :data:`~untaped.sdk.diagnostic.NOT_COUNTED` holds a
    :class:`~untaped.sdk.diagnostic.Failure`: the failure is remembered from
    an earlier run. A 12.0 command's printed rows go through it; an 11.x
    command still decides with ``finish``.
    """
    scope = _scope()
    if scope is None:
        return
    for row in rows:
        if not isinstance(row, OutcomeRecord) or not row.failed:
            continue
        remembered = any(
            NOT_COUNTED in info.metadata and isinstance(getattr(row, name, None), Failure)
            for name, info in type(row).model_fields.items()
        )
        if not remembered:
            scope.note(ExitCode.FAILURE)


def _row_error_code(error: object) -> int | None:
    """The exit code a dumped ``Failure`` mapping stands for (``None``: not one)."""
    if not isinstance(error, Mapping):
        return None
    code = error.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    try:
        return int(ErrorCategory(str(error.get("category"))).exit_code)
    except ValueError:
        return None


def failure_exit_code(*codes: int) -> int:
    """The exit code of a failed run: the most severe of ``codes`` and every counted failure.

    Never below ``1``: a failed run whose failures carry no category exits ``1``.
    """
    scope = _scope()
    code = combine_exit_codes(*codes, scope.worst if scope is not None else ExitCode.OK)
    return code if code not in (ExitCode.OK, ExitCode.PREDICATE) else ExitCode.FAILURE


def exit_on_counted_failure() -> None:
    """Exit with the worst code the invocation counted, if it counted a failure.

    A command that emitted a failure and returned normally still fails:
    the exit code is derived from what was emitted.
    """
    scope = _scope()
    if scope is not None and scope.worst != ExitCode.OK:
        raise SystemExit(scope.worst)


def derived_exit_code(code: str | int | None) -> str | int | None:
    """The code a run exiting with ``code`` ends with, given the failures it counted.

    The most severe of the two wins (a predicate hit, ``3``, never hides a
    counted failure); a code that is not a number is left alone.
    """
    scope = _scope()
    if scope is None or scope.worst == ExitCode.OK:
        return code
    if code is None:
        return scope.worst
    if isinstance(code, str):
        return code
    return combine_exit_codes(code, scope.worst)


def failure_text(error: UntapedError | Failure, *, item: str | None = None) -> str:
    """A failure's text line: ``error: [<item>: ]<message>``, then its ``hint:`` line."""
    prefix = "" if item is None else f"{item}: "
    if isinstance(error, UntapedError):
        body = format_error(error)
    else:
        body = (
            error.message if error.hint is None else f"{error.message}\n{_HINT_PREFIX}{error.hint}"
        )
    return f"error: {prefix}{body}"


class Diagnostics:
    """The stderr handle a command holds as ``ctx.diag``: one method per level.

    Every call resolves the current invocation's sink when it is made, never
    when the handle is built, so a handle kept in a module constant still
    writes to the invocation that uses it. ``--quiet`` mutes ``info``,
    ``success`` and progress; a failure counts toward the exit code.
    """

    def emit(self, diagnostic: Diagnostic) -> None:
        """Emit any registered kind, core's or a plugin's own (``kind=`` declared)."""
        current_sink().emit(diagnostic)

    def info(self, text: str | Text, *, hint: str | None = None, unmuted: bool = False) -> None:
        """An ``info`` line; a Rich ``Text`` renders styled on a terminal, plain elsewhere.

        ``-q`` mutes commentary: lines the user did not ask for and need not
        act on. A preview before a prompt, a stream the user asked to watch,
        and output a flag exists to produce are not commentary and print
        under ``-q``. Warnings, failures and hints always print.

        Pass ``unmuted=True`` for a line that is not commentary. A blank
        ``text`` prints an empty line in text (a separator) and nothing under
        JSON.
        """
        if isinstance(text, str):
            current_sink().emit(Message(level="info", message=text, hint=hint), unmuted=unmuted)
            return
        tail = "" if hint is None else f"\n{_HINT_PREFIX}{hint}"

        def styled(color: bool) -> str:
            return render_styled(text, colorize=color) + tail

        current_sink().emit(
            Message(level="info", message=text.plain, hint=hint), rendered=styled, unmuted=unmuted
        )

    def success(self, text: str) -> None:
        """A ``success`` line, muted by ``--quiet`` like ``info``."""
        current_sink().emit(Message(level="success", message=text))

    def warning(self, text: str, *, hint: str | None = None) -> None:
        """A ``warning:`` line, never muted."""
        current_sink().emit(Message(level="warning", message=text, hint=hint))

    def hint(self, text: str) -> None:
        """A ``hint:`` line: what to do next, never muted."""
        current_sink().emit(Message(level="hint", message=text))

    def failure(self, error: UntapedError | Failure, *, item: str | None = None) -> Failure:
        """Emit ``error`` as an ``error:`` line (``item`` names a per-item one) and return it.

        It counts toward the invocation's exit code.
        """
        failure = error if isinstance(error, Failure) else Failure.from_exception(error)
        if kind_of(type(failure)) is None:
            # A kind-less subclass (a payload nested in rows) prints as a plain failure.
            failure = Failure.model_validate(
                {name: getattr(failure, name) for name in Failure.model_fields}
            )
        if item is not None:
            failure = failure.model_copy(update={"item": item})
        current_sink().emit(failure, rendered=failure_text(error, item=item))
        return failure

    def relay(self, text: str) -> None:
        """Print another tool's output (a hook's stderr, a job log) as is, never counted.

        Unlike ``echo(err=True)``, an ``error:`` line it relays is not a failure.
        """
        current_sink().relay(text)

    def progress(self, label: str) -> AbstractContextManager[ProgressHandle]:
        """Report progress for a blocking operation: a spinner on a terminal, else lines.

        On a pipe and under JSON, ``untaped.progress`` lines start after 2 s,
        then come per phase, per 10 % and every 30 s while a step waits, and
        end with one ``done`` or ``failed`` line; ``--verbose`` prints every
        update. Pass the handle on: an inner layer reports through it.
        """
        return current_sink().progress(label)
