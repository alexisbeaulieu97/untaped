"""Shared helpers for the screen tests: a recording host and a small configurable screen."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from rich.console import RenderableType

from untaped.screen.core import Binding, Cmd, Frame, Screen
from untaped.screen.runtime import CmdKind
from untaped.testing.screens import rendered_text


class FakeHost:
    """Records what the runtime asks for; runs jobs on real threads when asked.

    With ``threads=False`` a spawned job waits until the test calls
    :meth:`run_jobs`. Calls the jobs post are collected and run by the test
    thread through :meth:`deliver`, standing in for the event loop thread.
    """

    def __init__(self, *, threads: bool = False) -> None:
        self.threads = threads
        self.spawned: list[CmdKind] = []
        self.started: list[threading.Thread] = []
        self.redraws = 0
        self.finishes = 0
        self._pending: list[Callable[[], None]] = []
        self._posted: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()

    def spawn(self, job: Callable[[], None], *, kind: CmdKind) -> None:
        self.spawned.append(kind)
        if self.threads:
            thread = threading.Thread(target=job, name=f"fake-{kind}", daemon=True)
            self.started.append(thread)
            thread.start()
        else:
            self._pending.append(job)

    def post(self, call: Callable[[], None]) -> None:
        self._posted.put(call)

    def redraw(self) -> None:
        self.redraws += 1

    def finish(self) -> None:
        self.finishes += 1

    def run_jobs(self) -> None:
        """Run every spawned job that has not run yet, on the calling thread."""
        while self._pending:
            self._pending.pop(0)()

    def deliver(self, count: int = 1, *, timeout: float = 5.0) -> None:
        """Run ``count`` posted calls on the calling (loop) thread, waiting for each."""
        for _ in range(count):
            self._posted.get(timeout=timeout)()

    def deliver_all(self) -> None:
        """Run the jobs and every posted call until nothing is left (no threads)."""
        while self._pending or not self._posted.empty():
            self.run_jobs()
            while not self._posted.empty():
                self._posted.get_nowait()()

    def join(self) -> None:
        for thread in self.started:
            thread.join(timeout=5)


@dataclass(frozen=True)
class Model:
    """What the test screens keep: what they saw and one flag."""

    log: tuple[object, ...] = ()
    flag: bool = False


type Handler = Callable[[Model, object], tuple[Model, Sequence[Cmd]] | None]


class Probe:
    """Builds a screen whose ``update`` records every message and defers to ``handler``.

    A message the handler returns ``None`` for is unhandled: same model object,
    no commands.
    """

    def __init__(
        self,
        handler: Handler | None = None,
        *,
        keys: tuple[Binding, ...] = (),
        init: Sequence[Cmd] = (),
        layout: str = "full",
    ) -> None:
        self.seen: list[object] = []
        self.threads: list[str] = []
        self._handler = handler
        self.screen: Screen[Model, str] = Screen(
            init=lambda: (Model(), init),
            update=self._update,
            view=self._view,
            title="Probe",
            command="untaped probe",
            alternative="untaped probe --format json",
            keys=keys,
            layout=layout,  # type: ignore[arg-type]
        )

    def _update(self, model: Model, message: object) -> tuple[Model, Sequence[Cmd]]:
        self.seen.append(message)
        self.threads.append(threading.current_thread().name)
        if self._handler is not None:
            result = self._handler(model, message)
            if result is not None:
                return result
        return model, []

    @staticmethod
    def _view(model: Model, frame: Frame) -> str:
        return f"seen {len(model.log)} messages, flag {model.flag}"


def logged(model: Model, message: object, *cmds: Cmd) -> tuple[Model, list[Cmd]]:
    """``model`` with ``message`` appended to its log, and ``cmds``."""
    return replace(model, log=(*model.log, message)), list(cmds)


def frame_text(renderable: RenderableType, width: int, height: int) -> str:
    """``renderable`` as plain text (what ``drive_screen`` frames hold)."""
    return rendered_text(renderable, width, height)


class ImmediateHost(FakeHost):
    """A host that runs jobs and posted calls at once, inside the call that issued them."""

    def spawn(self, job: Callable[[], None], *, kind: CmdKind) -> None:
        self.spawned.append(kind)
        job()

    def post(self, call: Callable[[], None]) -> None:
        call()
