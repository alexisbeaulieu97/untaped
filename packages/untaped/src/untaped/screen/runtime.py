"""The screen runtime: dispatch, commands and the frame, with no terminal in sight.

:class:`Runtime` holds a screen's model and applies messages to it on the loop
thread; everything it needs from the outside (threads, the event loop, redraws,
leaving the loop) goes through the small :class:`Host` protocol, so the same
code runs under the terminal adapter and under the synchronous
:class:`SyncHost` the test driver uses. It starts no threads of its own and
imports no prompt_toolkit.

What it owns, in one place:

- **Dispatch order** for a key: the screen's ``update`` first (the focused
  component lives there), then the screen's bindings, then the shared keys.
  "Handled" means ``update`` returned a different model object or any command.
- **Context.** Python 3.14 threads and ``call_soon_threadsafe`` callbacks do not
  inherit the caller's context variables, so the runtime owns the context:
  :meth:`Runtime.start` captures it, every call it posts to the host (a command's
  completion, hence the ``update`` it triggers and the commands that issues) runs
  in a copy of it, and each command runs in the context captured when it was
  issued. A chain of commands therefore sees the caller's variables throughout.
- **Commands**: a raising command becomes a :class:`CmdError`. Write commands
  run one at a time, in order, and finish even when the screen quits; the rest
  are abandoned on quit.
- **The frame**: the view above a pinned footer, or the help overlay.
"""

from __future__ import annotations

import contextvars
import io
from collections import deque
from collections.abc import Callable, Sequence
from typing import Literal, Protocol

from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.layout import Layout

from untaped.screen.core import (
    SHARED_KEYS,
    Binding,
    Cancel,
    Cmd,
    CmdError,
    Footer,
    Frame,
    Help,
    Key,
    Paste,
    Quit,
    Resize,
    Screen,
    is_inline,
)
from untaped.theme import ThemeSpec

__all__ = ["CmdKind", "Host", "Runtime", "SyncHost", "capture_console"]

type CmdKind = Literal["background", "write", "suspend"]


class Host(Protocol):
    """What the runtime needs from its environment."""

    def spawn(self, job: Callable[[], None], *, kind: CmdKind) -> None:
        """Run ``job`` off the loop thread (``suspend``: with the screen left)."""
        ...

    def post(self, call: Callable[[], None]) -> None:
        """Run ``call`` on the loop thread, later.

        The runtime hands over a ``call`` that already runs under the runtime's
        context, so the host need not (and cannot usefully) carry the poster's
        context variables across threads.
        """
        ...

    def redraw(self) -> None:
        """Paint the next frame."""
        ...

    def finish(self) -> None:
        """Leave the loop: the outcome is decided and no write is pending."""
        ...


def capture_console(
    width: int,
    height: int,
    *,
    color_system: Literal["standard", "256", "truecolor"] | None,
    no_color: bool,
) -> Console:
    """The one console that renders screens, into a buffer.

    Markup, emoji and the highlighter are off, so a view's plain ``str`` is
    literal text (a Jira summary like ``[WIP] fix [/]`` would otherwise raise);
    styling comes only from ``Text`` and renderables. The adapter and the test
    driver both build their console here so frames match what users see.
    """
    return Console(
        file=io.StringIO(),
        force_terminal=True,
        color_system=color_system,
        no_color=no_color,
        markup=False,
        emoji=False,
        highlight=False,
        legacy_windows=False,
        width=width,
        height=height,
    )


class Runtime[M, R]:
    """One running screen: the model, the dispatch and the commands in flight.

    Call :meth:`start` once, then :meth:`send` messages; all calls come from
    the loop thread. ``outcome`` is the first :class:`Quit` or :class:`Cancel`
    the screen produced; ``Host.finish`` is called once it is set and no write
    command is pending.
    """

    model: M
    outcome: Quit[R] | Cancel | None

    def __init__(
        self, screen: Screen[M, R], host: Host, *, theme: ThemeSpec, size: tuple[int, int]
    ) -> None:
        self.screen = screen
        self.host = host
        self.theme = theme
        self.size = size
        self.outcome = None
        self.help_open = False
        self._inbox: deque[object] = deque()
        self._draining = False
        self._writes: deque[tuple[Cmd, contextvars.Context]] = deque()
        self._suspends: deque[tuple[Cmd, contextvars.Context]] = deque()
        self._write_running = False
        self._suspend_running = False
        self._finished = False
        self._context = contextvars.Context()

    @property
    def saving(self) -> bool:
        """Whether the screen quit and the runtime waits for write commands."""
        # ``bool()`` keeps the property a bool: ``or`` would hand back the deque.
        return self.outcome is not None and (self._write_running or bool(self._writes))

    def start(self) -> None:
        """Capture the caller's context, run ``screen.init`` and issue its commands."""
        self._context = contextvars.copy_context()
        model, cmds = self.screen.init()
        self.model = model
        self._issue(cmds)
        self._drain()
        self._settle()

    def send(self, message: object) -> None:
        """Apply ``message`` (and any inline messages it causes), then redraw."""
        self._inbox.append(message)
        if self._draining:
            return
        self._drain()
        self._settle()

    def renderable(self) -> RenderableType:
        """The current frame: the view (or the help overlay) above a pinned footer."""
        width, height = self.size
        frame = Frame(width, max(1, height - 1), self.theme)
        footer = Footer(
            tuple(self._active_bindings()), saving=self.saving, labels=self._shared_labels()
        )
        inline = self.screen.layout == "inline"
        if self.help_open:
            overlay = footer.overlay(frame)
            body: RenderableType = overlay if inline else Align.center(overlay, vertical="middle")
        else:
            body = self.screen.view(self.model, frame)
        line = footer.line(frame)
        if inline:
            return Group(body, line)
        layout = Layout()
        layout.split_column(Layout(body, name="body", ratio=1), Layout(line, name="footer", size=1))
        return layout

    # --- dispatch -------------------------------------------------------------

    def _drain(self) -> None:
        if self._draining:
            return
        self._draining = True
        try:
            while self._inbox:
                self._dispatch(self._inbox.popleft())
        finally:
            self._draining = False

    def _dispatch(self, message: object) -> None:
        if isinstance(message, Resize):
            self.size = (message.width, message.height)
        if self.outcome is not None:
            return
        if isinstance(message, Quit | Cancel):
            self.outcome = message
        elif isinstance(message, Key):
            self._key(message.name)
        elif isinstance(message, Paste) and self.help_open:
            return
        else:
            self._update(message)

    def _update(self, message: object) -> bool:
        """Deliver ``message`` to ``update``; true when it was handled."""
        old = self.model
        model, cmds = self.screen.update(old, message)
        self.model = model
        self._issue(cmds)
        return model is not old or bool(cmds)

    def _key(self, name: str) -> None:
        if self.help_open:
            self._help_key(name)
            return
        if self._update(Key(name)):
            return
        for binding in self._active_bindings():
            if binding.key == name:
                if binding.message is not None:
                    self._update(binding.message)
                return
        shared = SHARED_KEYS.get(name)
        if shared is None:
            return
        if shared.message is None:
            self.help_open = True
            self._update(Help())
        elif not self._update(shared.message) and shared.unhandled is not None:
            self._inbox.append(shared.unhandled)

    def _help_key(self, name: str) -> None:
        if name in ("?", "esc", "enter"):
            self.help_open = False
        elif name == "ctrl-c":
            self._inbox.append(Cancel(interrupted=True))

    def _shared_labels(self) -> dict[str, str]:
        """The screen's shared-key labels for the current model (a function may decline)."""
        labels: dict[str, str] = {}
        for key, label in self.screen.shared_labels.items():
            text = label(self.model) if callable(label) else label
            if text is not None:
                labels[key] = text
        return labels

    def _active_bindings(self) -> list[Binding]:
        return [b for b in self.screen.keys if b.when is None or b.when(self.model)]

    # --- commands -------------------------------------------------------------

    def _issue(self, cmds: Sequence[Cmd]) -> None:
        for cmd in cmds:
            self._issue_one(cmd)

    def _issue_one(self, cmd: Cmd) -> None:
        if is_inline(cmd):
            message = cmd.fn()
            if message is not None:
                self._inbox.append(message)
            return
        context = contextvars.copy_context()
        if cmd.write:
            self._writes.append((cmd, context))
            self._pump()  # start it now, so commands begin in the order they were issued
        elif cmd.suspend:
            self._suspends.append((cmd, context))
            self._pump()
        else:
            self._spawn(cmd, context, "background")

    def _pump(self) -> None:
        """Start the next write or suspend command when none of its kind runs."""
        if self.outcome is not None:
            self._suspends.clear()
        if not self._write_running and self._writes:
            cmd, context = self._writes.popleft()
            self._write_running = True
            self._spawn(cmd, context, "write")
        if not self._suspend_running and self._suspends:
            cmd, context = self._suspends.popleft()
            self._suspend_running = True
            self._spawn(cmd, context, "suspend")

    def _spawn(self, cmd: Cmd, context: contextvars.Context, kind: CmdKind) -> None:
        def job() -> None:
            result: object = None
            try:
                result = context.run(cmd.fn)
            except Exception as error:
                result = CmdError(error)
            finally:
                # A fresh copy per call: a context cannot be entered twice at once.
                self.host.post(lambda: self._context.copy().run(self._complete, kind, result))

        self.host.spawn(job, kind=kind)

    def _complete(self, kind: CmdKind, result: object) -> None:
        """A command finished (loop thread): deliver its message unless the screen quit."""
        if kind == "write":
            self._write_running = False
        elif kind == "suspend":
            self._suspend_running = False
        if result is not None:  # a message after the outcome is dropped by ``_dispatch``
            self._inbox.append(result)
            self._drain()
        self._pump()
        self._settle()

    def _settle(self) -> None:
        """Leave the loop when the outcome is decided and no write is pending, else redraw."""
        if self._finished:
            return
        if self.outcome is not None and not self.saving:
            self._finished = True
            self.host.finish()
            return
        self.host.redraw()


class SyncHost:
    """A host with no threads: jobs and posted calls wait in two FIFO queues.

    The test driver calls :meth:`pump` after every key, which runs queued jobs
    and posted calls until both queues are empty; that is what "commands run
    synchronously" means.
    """

    def __init__(self) -> None:
        self._jobs: deque[Callable[[], None]] = deque()
        self._calls: deque[Callable[[], None]] = deque()
        self.finished = False

    def spawn(self, job: Callable[[], None], *, kind: CmdKind) -> None:
        self._jobs.append(job)

    def post(self, call: Callable[[], None]) -> None:
        self._calls.append(call)

    def redraw(self) -> None:
        """Nothing to paint: the driver renders frames itself."""

    def finish(self) -> None:
        self.finished = True

    def pump(self) -> None:
        """Run queued jobs and posted calls until both queues are empty."""
        while self._jobs or self._calls:
            while self._jobs:
                self._jobs.popleft()()
            while self._calls:
                self._calls.popleft()()
