"""The one writer to stderr: the invocation's scope, its sink, and the spinner.

:func:`diagnostics_scope` scopes one invocation: its :class:`OutputPolicy`
(inherited from an enclosing scope, so a nested scope starts with what argv
asked for), its sink, and the most severe failure it counted. Every stderr
line is a :class:`~untaped.output.diagnostic.Diagnostic` emitted through
:func:`current_sink`, which renders it as text or as one JSON line (``level``
and ``kind`` first) per the policy, and writes it in one ``write()`` under
one process-wide lock, clearing and redrawing the spinner around it.

Rules the sink keeps:

- **Counting.** Emitting a :class:`~untaped.output.diagnostic.Failure` (a
  subclass too) counts its ``exit_code`` toward the scope's exit code
  (:func:`failure_exit_code`); a ``StaleData`` whose ``error`` is set is a
  warning and counts nothing.
- **Muting.** ``--quiet`` mutes ``info``, ``success`` and progress, never a
  warning, a failure or a hint; ``debug`` prints only under ``--verbose``.
- **Never dropped.** Off its scope a line reaches the most recently entered
  live scope (``untaped.output.policy`` says how, and its one documented
  limit); with no scope at all it goes to a default text sink. ``emit`` on a
  closed sink writes the line plainly. The stream is ``sys.stderr`` read at
  each write, so ``capsys`` and ``redirect_stderr`` see it.
- **No deadlock.** The lock is never held across rendering, a callback or a
  thread ``join()``; nothing in this package logs; and a nested write from a
  thread that already holds the lock writes without taking it.
- **Screens.** While a screen owns the terminal (:meth:`Sink.hold`), text
  lines are held and printed after it exits; JSON lines are not held.

A presentation (``rendered``) may accompany a diagnostic: the exact text the
text renderer prints (a callable taking whether to colour, or a string). It
is not on the wire and never reaches a JSON line. It keeps today's text
byte-identical: themed ``ui.message`` lines, verbatim ``echo`` lines.
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol, TextIO

from pydantic import BaseModel

from untaped.errors import ErrorCategory, ExitCode, UntapedError, combine_exit_codes
from untaped.output.diagnostic import (
    Debug,
    Diagnostic,
    Failure,
    Message,
    StaleData,
    format_error,
    kind_of,
    split_hint,
)
from untaped.output.policy import (
    OutputPolicy,
    context_holder,
    current_holder,
    current_policy,
    enter,
    leave,
    unscoped_policy,
)
from untaped.output.progress import ProgressHandle
from untaped.redaction import redact_url_password
from untaped.render import render_styled, should_colorize, stream_is_tty

if TYPE_CHECKING:
    from rich.text import Text

type Rendered = Callable[[bool], str] | str
"""A line's text presentation: a string, or a callable given whether to colour."""

_HINT_PREFIX = "hint: "
_ECHO_LEVELS = (("warning: ", "warning"), (_HINT_PREFIX, "hint"))

# --------------------------------------------------------------------------- #
# The writer: one lock, one write per line, the spinner cleared and redrawn
# --------------------------------------------------------------------------- #

_WRITE_LOCK = threading.Lock()
_thread = threading.local()
#: Open spinners, innermost last; only the last one draws. Guarded by the lock.
_spinners: list[_SpinnerHandle] = []
#: The width of the spinner line on screen (0: none drawn). Guarded by the lock.
_drawn = 0


def _write(text: str) -> None:
    """Write ``text`` to stderr in one ``write()``, around the spinner, under the lock.

    A write from a thread already inside the lock (a re-entrant call) goes
    straight to the stream instead of blocking on itself.
    """
    if getattr(_thread, "inside", False):
        _raw(text)
        return
    with _WRITE_LOCK:
        _thread.inside = True
        try:
            _raw(_framed(text))
        finally:
            _thread.inside = False


def _raw(text: str) -> None:
    stream = sys.stderr
    stream.write(text)
    stream.flush()


def _framed(text: str) -> str:
    """``text`` preceded by clearing the spinner line and followed by redrawing it."""
    global _drawn
    out = f"\r{' ' * _drawn}\r" if _drawn else ""
    _drawn = 0
    out += text
    if _spinners:
        line = _spinners[-1].frame()
        out += f"\r{line}"
        _drawn = len(line)
    return out


_SPINNER_TICK = 0.1
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


class _TaskState:
    """What one task last reported, and how its line reads (the update rules of the handle)."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.phase: str | None = None
        self.done: int | None = None
        self.total: int | None = None
        self.message: str | None = None

    def apply(
        self, message: str | None, phase: str | None, done: int | None, total: int | None
    ) -> bool:
        """Record one update; ``True`` when it starts a new phase.

        ``None`` keeps ``phase``, ``done`` and ``total``; a new phase resets
        the counts to what this update passes; ``message`` is per update.
        """
        new_phase = phase is not None and phase != self.phase
        if new_phase:
            self.phase, self.done, self.total = phase, None, None
        if done is not None:
            self.done = done
        if total is not None:
            self.total = total
        self.message = message
        return new_phase

    def step(self) -> int | None:
        """The 10 % step the counts reached, or ``None`` without a ``total``."""
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
        return f"{label}: {state}{tail} ({int(elapsed)}s)"
    line = label if phase is None else f"{label} · {phase}"
    if done is not None:
        line += f" {done}" if total is None else f" {done}/{total}"
    return f"{line}{tail} ({int(elapsed)}s)"


class _SpinnerHandle:
    """An animated spinner on a terminal: a ticker thread redraws it under the lock.

    Every line written while it is up clears it and redraws it after
    (:func:`_write`); only the innermost open spinner draws.
    """

    def __init__(self, label: str) -> None:
        self._task = _TaskState(label)
        self._frames = _spinner_frames(sys.stderr)
        self._frame = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="untaped-spinner", daemon=True)
        self._started_at = time.monotonic()

    def frame(self) -> str:
        """The next frame of the spinner line (called under the lock)."""
        glyph = self._frames[self._frame % len(self._frames)]
        self._frame += 1
        task = self._task
        line = _compose(
            task.label,
            phase=task.phase,
            done=task.done,
            total=task.total,
            text=task.message,
            elapsed=time.monotonic() - self._started_at,
        )
        return _fit(f"{glyph} {line}")

    def start(self) -> None:
        with _WRITE_LOCK:
            _spinners.append(self)
            _raw(_framed(""))
        self._thread.start()

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        with _WRITE_LOCK:
            self._task.apply(message, phase, done, total)
            _raw(_framed(""))

    def finish(self, *, failed: bool) -> None:
        del failed
        self._stop.set()
        self._thread.join()  # never under the lock: the ticker takes it
        with _WRITE_LOCK:
            if self in _spinners:
                _spinners.remove(self)
            _raw(_framed(""))

    def _run(self) -> None:
        while not self._stop.wait(_SPINNER_TICK):
            with _WRITE_LOCK:
                if _spinners and _spinners[-1] is self:
                    _raw(_framed(""))


# --------------------------------------------------------------------------- #
# The line backends
# --------------------------------------------------------------------------- #

_THROTTLE_INTERVAL = 2.0
_STEP_PERCENT = 10


class Backend(Protocol):
    """What renders one progress block."""

    def start(self) -> None: ...

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None: ...

    def finish(self, *, failed: bool) -> None: ...


class _SilentHandle:
    """Progress for ``--quiet``: no progress output."""

    def start(self) -> None:
        return None

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        return None

    def finish(self, *, failed: bool) -> None:
        return None


class _LineHandle:
    """Newline-terminated progress lines; throttled unless ``verbose``."""

    def __init__(self, label: str, *, write: Callable[[str], None], verbose: bool) -> None:
        self._task = _TaskState(label)
        self._write = write
        self._verbose = verbose
        self._started_at = time.monotonic()
        self._last_emit: float | None = None
        self._last_step = -1

    def start(self) -> None:
        self._emit()

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        if self._task.apply(message, phase, done, total):
            self._last_emit = None
            self._last_step = -1
        step = self._task.step()
        now = time.monotonic()
        due = self._last_emit is None or now - self._last_emit >= _THROTTLE_INTERVAL
        stepped = step is not None and step > self._last_step
        if not (self._verbose or due or stepped):
            return
        if step is not None:
            self._last_step = max(step, self._last_step)
        self._emit()

    def finish(self, *, failed: bool) -> None:
        return None

    def _emit(self) -> None:
        self._last_emit = now = time.monotonic()
        task = self._task
        line = _compose(
            task.label,
            phase=task.phase,
            done=task.done,
            total=task.total,
            text=task.message,
            elapsed=now - self._started_at,
        )
        self._write(f"{line}\n")


def _make_handle(
    label: str,
    *,
    write: Callable[[str], None],
    verbose: bool,
    quiet: bool,
    tty: bool,
) -> Backend:
    if quiet:
        return _SilentHandle()
    if tty and not verbose:
        return _SpinnerHandle(label)
    return _LineHandle(label, write=write, verbose=verbose)


@contextmanager
def _progress_reporter(
    label: str,
    *,
    write: Callable[[str], None],
    verbose: bool,
    quiet: bool,
    tty: bool,
) -> Iterator[ProgressHandle]:
    """Report progress for a blocking operation, writing lines through ``write``.

    The spinner line is cleared on exit, success or failure, so any
    propagating error prints cleanly.
    """
    handle = _make_handle(label, write=write, verbose=verbose, quiet=quiet, tty=tty)
    handle.start()
    failed = False
    try:
        yield handle
    except BaseException:
        failed = True
        raise
    finally:
        handle.finish(failed=failed)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _level(diagnostic: Diagnostic) -> str:
    return str(getattr(diagnostic, "level", "info"))


def _text(diagnostic: Diagnostic) -> str:
    """The text line of a diagnostic emitted without a presentation."""
    level = _level(diagnostic)
    if isinstance(diagnostic, Debug):
        return f"DEBUG {diagnostic.logger}: {diagnostic.message}"
    message = diagnostic.message
    if isinstance(diagnostic, Failure) and diagnostic.item is not None:
        message = f"{diagnostic.item}: {message}"
    line = f"{level}: {message}" if level in ("error", "warning", "hint") else message
    if diagnostic.hint is not None:
        line += f"\n{_HINT_PREFIX}{diagnostic.hint}"
    return line


def _json(diagnostic: Diagnostic) -> str:
    """One JSON line: ``level`` and ``kind``, then the diagnostic's fields."""
    record = {"level": _level(diagnostic), "kind": kind_of(type(diagnostic))}
    return json.dumps(record | diagnostic.model_dump(mode="json"), default=str)


def echo_diagnostic(text: str) -> Diagnostic | None:
    """The diagnostic an ``echo(text, err=True)`` line stands for (``None``: a blank one).

    An ``error:`` prefix is a :meth:`~untaped.output.diagnostic.Failure.plain`
    (its ``hint:`` lines become ``hint``); ``warning:`` and ``hint:`` are a
    :class:`~untaped.output.diagnostic.Message` of that level; anything else
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
        """Report progress for a blocking operation (``verbose``/``quiet`` add to the policy's)."""
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


#: Set by ``untaped.testing``: a scope entered meanwhile also records what it emits.
_recorder: ContextVar[list[Diagnostic] | None] = ContextVar("untaped_recorder", default=None)


@contextmanager
def recording() -> Iterator[list[Diagnostic]]:
    """Record every diagnostic emitted in the scopes entered inside the block.

    The scopes still write to stderr as they would (a tee), so captured text
    and the records agree.
    """
    captured: list[Diagnostic] = []
    token = _recorder.set(captured)
    try:
        yield captured
    finally:
        _recorder.reset(token)


class _ScopeSink:
    """The sink of one scope (or, with no scope, of the default ``_Unscoped`` policy)."""

    def __init__(self, scope: _Scope | None) -> None:
        self._scope = scope
        self._recorder = _recorder.get() if scope is not None else None
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
        self, diagnostic: Diagnostic, rendered: Rendered | None, *, unmuted: bool, end: str
    ) -> None:
        if kind_of(type(diagnostic)) is None:
            raise TypeError(
                f"{type(diagnostic).__qualname__} declares no kind, so it is not a line: "
                "emit a subclass declared with kind=…"
            )
        if isinstance(diagnostic, StaleData) and not self._first_stale(diagnostic):
            return
        if isinstance(diagnostic, Failure) and self._scope is not None:
            self._scope.note(diagnostic.exit_code)
        policy = self.policy
        level = _level(diagnostic)
        if isinstance(diagnostic, Debug) and not policy.verbose:
            return
        if policy.quiet and not unmuted and level in ("info", "success", "progress"):
            return
        if self._recorder is not None:  # what was shown, as the stderr tee shows it
            self._recorder.append(diagnostic)
        if policy.diagnostics == "json":
            _write(_json(diagnostic) + "\n")
            return
        if callable(rendered):
            text = rendered(should_colorize(sys.stderr))
        else:
            text = rendered if rendered is not None else _text(diagnostic)
        self._out(text + end)

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
        return _progress_reporter(
            label,
            write=self._out,
            verbose=verbose or policy.verbose,
            quiet=quiet or policy.quiet or policy.diagnostics == "json",
            tty=stream_is_tty(sys.stderr),
        )

    @contextmanager
    def hold(self) -> Iterator[None]:
        with self._state_lock:
            self._holding += 1
        try:
            yield
        finally:
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

    def _out(self, text: str) -> None:
        """Write text output, or keep it while a screen holds the terminal."""
        with self._state_lock:
            if self._holding and not self._closed:
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
    """Scope one invocation: its policy (an enclosing scope's, else the unscoped one) and sink."""
    parent = context_holder()
    policy = replace(parent.policy) if parent is not None else unscoped_policy()
    scope = _Scope(policy)
    token = enter(scope)
    try:
        yield
    finally:
        leave(scope, token)
        scope.sink.close()


def count_row_failures(rows: Iterable[object]) -> None:
    """Count each failed row toward the invocation's exit code, as emitting it does.

    A model row counts every top-level field holding a
    :class:`~untaped.output.diagnostic.Failure` (its ``error``, or a suite's
    ``failure``); a mapping row counts its ``error`` mapping by its
    ``exit_code`` or ``category``. A row marked failed with no error is not
    counted.
    """
    scope = _scope()
    if scope is None:
        return
    for row in rows:
        if isinstance(row, BaseModel):
            for name in type(row).model_fields:
                value = getattr(row, name, None)
                if isinstance(value, Failure):
                    scope.note(value.exit_code)
        elif isinstance(row, Mapping):
            code = _row_error_code(row.get("error"))
            if code is not None:
                scope.note(code)


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

    def info(self, text: str | Text, *, hint: str | None = None) -> None:
        """An ``info`` line; a Rich ``Text`` renders styled on a terminal, plain elsewhere."""
        if isinstance(text, str):
            current_sink().emit(Message(level="info", message=text, hint=hint))
            return
        tail = "" if hint is None else f"\n{_HINT_PREFIX}{hint}"

        def styled(color: bool) -> str:
            return render_styled(text, colorize=color) + tail

        current_sink().emit(Message(level="info", message=text.plain, hint=hint), rendered=styled)

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
        """Report progress for a blocking operation (a spinner on a terminal)."""
        return current_sink().progress(label)
