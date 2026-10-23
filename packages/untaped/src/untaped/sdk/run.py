"""The run: the per-invocation state a block reaches, and the handles built from it.

A block needs a few things only a run knows: the home folder, which plugin
owns which settings model, the settings the run picked, the stderr sink, the
terminal, the current project and the time. ``untaped.runtime`` builds one
:class:`Run` per invocation and sets it with :func:`run_scope`; the SDK reads
it with :func:`current_run` and never imports the runtime. A command sees it
as ``self.ctx`` (:class:`Context`) and ``self.settings``; code outside a
command uses :func:`settings_of` and :func:`plugin_of`, each naming a plugin
by its settings model.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from rich.text import Text

    from untaped.sdk._ui import UiContext
    from untaped.sdk.diagnostic import Diagnostic, Failure
    from untaped.sdk.errors import UntapedError
    from untaped.sdk.progress import ProgressHandle
    from untaped.sdk.projects import Project


class NoRunError(RuntimeError):
    """Code that needs the run was called outside one: a bug, never a user's mistake."""


class Diag(Protocol):
    """The stderr handle (``ctx.diag``): one method per level, written to the run's sink."""

    def emit(self, diagnostic: Diagnostic) -> None: ...

    def info(self, text: str | Text, *, hint: str | None = None, unmuted: bool = False) -> None: ...

    def success(self, text: str) -> None: ...

    def warning(self, text: str, *, hint: str | None = None) -> None: ...

    def hint(self, text: str) -> None: ...

    def failure(self, error: UntapedError | Failure, *, item: str | None = None) -> Failure: ...

    def relay(self, text: str) -> None: ...

    def progress(self, label: str) -> AbstractContextManager[ProgressHandle]: ...


class Clock:
    """The run's time: wall time, monotonic time and what a wait sleeps on."""

    def now(self) -> datetime:
        """The current wall time, in UTC."""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Seconds on a clock that never goes back, for durations."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Wait ``seconds``."""
        time.sleep(seconds)


@dataclass(frozen=True)
class Composition:
    """Which plugin owns what: its settings model, and the classes under its package."""

    #: The plugin each composed settings model identifies.
    owners: Mapping[type[BaseModel], str] = field(default_factory=dict)
    #: The plugin owning each top-level package (an entry point's module's).
    packages: Mapping[str, str] = field(default_factory=dict)

    def owner_of_settings(self, model: type[BaseModel]) -> str:
        """The plugin whose settings model ``model`` is; :class:`LookupError` when none is."""
        try:
            return self.owners[model]
        except KeyError:
            raise LookupError(
                f"{model.__qualname__} is the settings model of no composed plugin"
            ) from None

    def owner_of_module(self, module: str) -> str:
        """The plugin owning the classes defined in ``module``; :class:`LookupError` when none."""
        try:
            return self.packages[module.partition(".")[0]]
        except KeyError:
            raise LookupError(f"no composed plugin owns module {module}") from None


@dataclass(frozen=True, kw_only=True)
class Run:
    """One invocation's state, built by the runtime and read by the blocks."""

    home: Path
    composition: Composition
    #: The settings the run picked: a plugin's section, typed, by section name.
    picks: Callable[[str, type[BaseModel]], BaseModel]
    diag: Diag
    #: The terminal handle, built on first use (prompts and screens need one).
    terminal: Callable[[], UiContext]
    clock: Clock = field(default_factory=Clock)


_RUN: ContextVar[Run | None] = ContextVar("untaped_run", default=None)


@contextmanager
def run_scope(run: Run) -> Iterator[Run]:
    """Make ``run`` the current run for the block inside, then restore the one before."""
    token = _RUN.set(run)
    try:
        yield run
    finally:
        _RUN.reset(token)


def current_run() -> Run:
    """The current run; :class:`NoRunError` outside one."""
    run = _RUN.get()
    if run is None:
        raise NoRunError(
            "no untaped run is active: the runtime sets one per invocation, and a test "
            "sets one with untaped.sdk.run.run_scope"
        )
    return run


@dataclass(frozen=True)
class PluginHandle:
    """A plugin, for code acting on its behalf: its folder and the files it names."""

    name: str
    home: Path

    @property
    def dir(self) -> Path:
        """``~/.untaped/plugins/<name>/``: the plugin's own data. Not created here."""
        return self.home / "plugins" / self.name

    def committed(self, name: str, *, root: Path | None = None) -> Path:
        """``<root>/.untaped/<name>``: the plugin's files people write and may commit.

        ``name`` is the plugin's own: its name, ``<plugin>.<ext>`` or a path
        under ``<plugin>/``. ``root`` is the checkout's or project's root;
        ``None`` gives a path relative to the working directory.
        """
        from untaped.sdk.projects import plugin_committed_path  # noqa: PLC0415 - projects' cost

        return plugin_committed_path(self.name, name, root=Path() if root is None else root)


def plugin_of(model: type[BaseModel]) -> PluginHandle:
    """The plugin whose settings model is ``model``, wherever the code runs.

    Code that runs for another plugin (awx's lint command calling lint's
    loader) names that plugin by its model, never by who is running.
    """
    run = current_run()
    return PluginHandle(name=run.composition.owner_of_settings(model), home=run.home)


def settings_of[M: BaseModel](model: type[M]) -> M:
    """The settings of the plugin whose model is ``model``, as this run picked them."""
    run = current_run()
    section = run.picks(run.composition.owner_of_settings(model), model)
    if not isinstance(section, model):
        raise TypeError(f"the run picked {type(section).__qualname__} for {model.__qualname__}")
    return section


@dataclass(frozen=True)
class Context:
    """What a command reaches without declaring it: ``self.ctx``.

    ``diag`` writes to stderr; ``terminal`` is the terminal handle for
    prompts and screens; ``project`` is the current project or ``None``;
    ``plugin`` is the command's own plugin.
    """

    run: Run
    plugin: PluginHandle

    @property
    def diag(self) -> Diag:
        return self.run.diag

    @property
    def terminal(self) -> UiContext:
        return self.run.terminal()

    @property
    def project(self) -> Project | None:
        from untaped.sdk.projects import project_at  # noqa: PLC0415 - projects' cost

        return project_at(Path.cwd())


def context_for(cls: type) -> Context:
    """The :class:`Context` of a command class: the run, seen by the plugin defining ``cls``."""
    run = current_run()
    try:
        name = run.composition.owner_of_module(cls.__module__)
    except LookupError:
        raise LookupError(
            f"{cls.__qualname__} belongs to no composed plugin: its module {cls.__module__} "
            "is under no plugin's package"
        ) from None
    return Context(run=run, plugin=PluginHandle(name=name, home=run.home))
