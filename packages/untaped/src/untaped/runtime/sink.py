"""A run's sink: how each stderr line is counted, muted, rendered and written.

Every stderr line is a :class:`~untaped.sdk.diagnostic.Diagnostic` emitted
through the current run's sink (:func:`untaped.runtime.run_output.current_sink`),
which renders it as text or as one JSON line (``level`` and ``kind`` first)
per the run's policy, and writes it in one ``write()`` under the
process-wide write lock (:mod:`untaped.runtime.stderr`), clearing and
redrawing the spinner around it (:mod:`untaped.runtime.progress_stack`).

Rules the sink keeps:

- **Muting.** ``--quiet`` mutes ``info``, ``success`` and progress, never a
  warning, a failure or a hint; ``debug`` prints only under ``--verbose``.
- **Threads.** A worker writes to the run its context carries, so every
  thread untaped starts copies its starter's context (``bounded_map``,
  ``Parallel.map``, a screen's jobs). ``emit`` on a closed sink writes the
  line plainly. The stream is ``sys.stderr`` read at each write, so
  ``capsys`` and ``redirect_stderr`` see it.
- **No deadlock.** The lock is never held across a callback or a thread
  ``join()``; nothing in this package logs; and a nested write from a thread
  that already holds the lock writes without taking it.
- **Screens.** While a screen owns the terminal (:meth:`Sink.hold`), text
  lines are held and printed after it exits; JSON lines are not held.
  Progress is the exception: no spinner is drawn and running text progress
  lines are dropped, not held, while any open run holds a screen; a task's
  ``done`` or ``failed`` line is held like any other when its start line
  printed (held by its own run's sink: a screen another run holds does
  not hold it, as for any other line).

A presentation (``rendered``) may accompany a diagnostic: the exact text the
text renderer prints (a callable taking whether to colour, or a string). It
is not on the wire and never reaches a JSON line: a themed empty-table
note, a styled ``info`` line, an error with its ``hint:`` line, a debug log
record as its formatter writes it.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Literal, Protocol

from untaped.runtime.progress_stack import Mode, hold_screen, open_task, release_screen, write
from untaped.runtime.redaction import redact_url_password
from untaped.runtime.render import MessageKind, printable, render_message, should_colorize
from untaped.runtime.theme import BUILTIN_THEMES
from untaped.sdk._run import OutputPolicy, recorder
from untaped.sdk._streams import stream_is_tty
from untaped.sdk.diagnostic import (
    Debug,
    Deprecation,
    Diagnostic,
    Failure,
    Message,
    Progress,
    Stale,
    kind_of,
)
from untaped.sdk.progress import ProgressHandle

if TYPE_CHECKING:
    from untaped.runtime.run_output import RunOutput
    from untaped.sdk._theme import ThemeSpec

type Rendered = Callable[[bool], str] | str
"""A line's text presentation: a string, or a callable given whether to colour."""

HINT_PREFIX = "hint: "


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _level(diagnostic: Diagnostic) -> str:
    return str(getattr(diagnostic, "level", "info"))


def _text(diagnostic: Diagnostic, *, theme: ThemeSpec | None, color: bool) -> str:
    """The text line of a diagnostic emitted without a presentation.

    An ``info``, ``success`` or ``warning`` message and a deprecation render
    through the theme (``theme``: the policy's, else the default); its
    ``hint:`` line, a failure, a debug line and a stale-data line do not.
    Control characters in any of them show escaped (:func:`printable`).
    """
    level = _level(diagnostic)
    if isinstance(diagnostic, Debug):
        return printable(f"DEBUG {diagnostic.logger}: {diagnostic.message}")
    message = diagnostic.message
    hint = None if diagnostic.hint is None else printable(diagnostic.hint)
    themed = _themed_level(diagnostic)
    if themed is not None:
        line = render_message(
            themed,
            message,
            theme=theme or BUILTIN_THEMES["default"],
            colorize=color,
        )
        return line if hint is None else f"{line}\n{HINT_PREFIX}{hint}"
    if isinstance(diagnostic, Failure) and diagnostic.item is not None:
        message = f"{diagnostic.item}: {message}"
    message = printable(message)
    line = f"{level}: {message}" if level in ("error", "warning", "hint") else message
    if hint is not None:
        line += f"\n{HINT_PREFIX}{hint}"
    return line


def _themed_level(diagnostic: Diagnostic) -> MessageKind | None:
    """The theme level ``diagnostic`` renders through, if it renders through the theme."""
    if isinstance(diagnostic, Deprecation):
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


# --------------------------------------------------------------------------- #
# A run's output and its sink
# --------------------------------------------------------------------------- #


class Sink(Protocol):
    """Where an invocation's stderr lines go."""

    def emit(
        self, diagnostic: Diagnostic, *, rendered: Rendered | None = None, unmuted: bool = False
    ) -> None:
        """Count, render and write one line (``unmuted``: ``--quiet`` never mutes it)."""
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


class RunSink:
    """The sink of one run's output (or, off every run, the default text sink's)."""

    def __init__(self, output: RunOutput | None) -> None:
        self._output = output
        #: The test's recorder when the run was built (``Install.running()``'s ``diagnostics``).
        self._recorder = recorder() if output is not None else None
        self._held: list[str] = []
        self._holding = 0
        self._closed = False
        self._stale: set[tuple[str, str]] = set()
        self._state_lock = threading.Lock()

    @property
    def policy(self) -> OutputPolicy:
        return self._output.policy if self._output is not None else default_policy()

    def emit(
        self, diagnostic: Diagnostic, *, rendered: Rendered | None = None, unmuted: bool = False
    ) -> None:
        self._emit(diagnostic, rendered, unmuted=unmuted, end="\n")

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
                "list it in its plugin class's records"
            )
        if isinstance(diagnostic, Stale) and not self._first_stale(diagnostic):
            return
        if isinstance(diagnostic, Failure) and self._output is not None:
            self._output.note(diagnostic.exit_code)
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
            write(_json(diagnostic) + "\n")
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
        mode: Mode = "lines"
        if quiet or policy.quiet:
            mode = "silent"
        elif policy.diagnostics == "text" and not verbose and stream_is_tty(sys.stderr):
            mode = "spinner"
        return open_task(label, sink=self, policy=policy, mode=mode, verbose=verbose)

    def emit_progress(
        self, progress: Progress, policy: OutputPolicy, *, droppable: bool = True
    ) -> None:
        """Print one progress line per ``policy`` (the task's), muted by ``--quiet``.

        Under a screen a ``droppable`` line is dropped and any other is held.
        """
        self._emit(progress, None, unmuted=False, end="\n", droppable=droppable, policy=policy)

    @contextmanager
    def hold(self) -> Iterator[None]:
        with self._state_lock:
            self._holding += 1
        hold_screen()
        try:
            yield
        finally:
            release_screen()
            with self._state_lock:
                self._holding -= 1
                held = self._held if self._holding == 0 else []
                if self._holding == 0:
                    self._held = []
            for text in held:
                write(text)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            held, self._held, self._holding = self._held, [], 0
        for text in held:
            write(text)

    def _out(self, text: str, *, droppable: bool = False) -> None:
        """Write text, or hold it while a screen owns the terminal (``droppable``: drop it)."""
        with self._state_lock:
            if self._holding and not self._closed:
                if not droppable:
                    self._held.append(text)
                return
        write(text)

    def _first_stale(self, stale: Stale) -> bool:
        """Whether this is the run's first line about ``stale``'s cache entry."""
        key = (stale.cache, stale.key)
        with self._state_lock:
            if key in self._stale:
                return False
            self._stale.add(key)
            return True


#: Environment variable choosing the stderr format: ``json`` or ``text``.
DIAGNOSTICS_ENV = "UNTAPED_DIAGNOSTICS"


def forced_diagnostics() -> Literal["text", "json"] | None:
    """The stderr format ``UNTAPED_DIAGNOSTICS`` forces now (``None``: follow the format).

    A run reads it once, when it is built (``untaped.runtime.run.start_run``).
    """
    choice = os.environ.get(DIAGNOSTICS_ENV, "").strip().lower()
    if choice == "json":
        return "json"
    if choice == "text":
        return "text"
    return None


def default_policy() -> OutputPolicy:
    """The default text sink's policy: the environment's, read at each line."""
    return OutputPolicy(forced=forced_diagnostics())
