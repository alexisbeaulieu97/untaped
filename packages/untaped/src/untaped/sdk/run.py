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
    from untaped.sdk.plugin import SpelledCommand
    from untaped.sdk.plugin_records import InstalledPlugin
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


def _unspelled(spelled: SpelledCommand) -> str:
    """A command line for a run that composed no declaration: the class's name and arguments."""
    return " ".join([spelled.command.__name__, *map(str, spelled.args)])


@dataclass(frozen=True)
class Composition:
    """Which plugin owns what, and the plugins this install has."""

    #: The plugin each composed settings model identifies.
    owners: Mapping[type[BaseModel], str] = field(default_factory=dict)
    #: The plugin owning each top-level package (an entry point's module's).
    packages: Mapping[str, str] = field(default_factory=dict)
    #: This install's plugins as ``untaped plugin list`` shows them, read on first use.
    installed: Callable[[], tuple[InstalledPlugin, ...]] = tuple
    #: The command line a ``Command.spelled(...)`` stands for, as this run names commands.
    spell: Callable[[SpelledCommand], str] = _unspelled

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


@dataclass(kw_only=True)
class RunNotes:
    """What blocks note on a run while its command runs."""

    #: The command previewed through ``Confirm.as_dry_run()`` (a ``--check``).
    preview: bool = False


@dataclass(frozen=True)
class Rendering:
    """How the run renders stderr, fixed once before anything is emitted.

    The run decides it from what the line names (``ctx.run.rendering``),
    and the sink renders by it, so a plugin has no reason to read it:
    branching on JSON around the sink would split what one line says.
    ``format`` is the ``--format`` the user chose (``None``: the command's
    own default), ``source`` where it came from (``--format``,
    ``UNTAPED_FORMAT`` or ``untaped.ui.format``), ``theme`` the
    ``untaped.ui.theme`` text messages render with (the runtime's theme
    model; ``None``: the default theme, as under JSON diagnostics).
    """

    format: str | None = None
    source: str | None = None
    theme: BaseModel | None = None


#: What a run reads before its composition is attached: no plugin owns anything.
_UNCOMPOSED = Composition()


@dataclass(frozen=True, kw_only=True)
class Run:
    """One invocation's state, built by the runtime and read by the blocks.

    The runtime starts it before anything else and attaches the composition
    once it is built (:meth:`attach`); until then no plugin owns anything.
    """

    #: Where the home is, asked on each read of :attr:`home` (the runtime caches it):
    #: a home that doesn't resolve fails the read, never the start of the run.
    locate_home: Callable[[], Path]
    #: Who owns what among the composed plugins: no plugin owns anything until
    #: the runtime attaches the composition (:meth:`attach`).
    composition: Composition = _UNCOMPOSED
    #: The settings the run picked: a plugin's section, typed, by section name.
    picks: Callable[[str, type[BaseModel]], BaseModel]
    diag: Diag
    #: The terminal handle, built on first use (prompts and screens need one).
    terminal: Callable[[], UiContext]
    #: What every block reads time from (:func:`run_clock`).
    clock: Clock = field(default_factory=Clock)
    #: What blocks noted during ``run``, for core to read after it.
    notes: RunNotes = field(default_factory=RunNotes)
    #: How stderr renders: fixed once by the runtime (:meth:`render_with`) for
    #: a declared command or a line naming none; ``None`` until then, and for
    #: an 11.x command, whose parse still decides it.
    rendering: Rendering | None = None

    @property
    def home(self) -> Path:
        """The home folder; raises the error that keeps it from resolving."""
        return self.locate_home()

    def attach(self, composition: Composition) -> Run:
        """Attach the run's one composition once it is built (it is frozen after); the run."""
        if self.composition is not _UNCOMPOSED:
            raise RuntimeError("a run's composition is attached once")
        object.__setattr__(self, "composition", composition)  # frozen to every other writer
        return self

    def render_with(self, rendering: Rendering) -> Run:
        """Fix how the run renders stderr, once, before anything is emitted; the run."""
        if self.rendering is not None:
            raise RuntimeError("a run's rendering is fixed once")
        object.__setattr__(self, "rendering", rendering)  # frozen to every other writer
        return self


_RUN: ContextVar[Run | None] = ContextVar("untaped_run", default=None)

#: Code outside a run reads a plain clock.
_PLAIN = Clock()
#: The clock a test installed (:func:`installed_clock`), read before any run's.
_installed: list[Clock | None] = [None]


def run_clock() -> Clock:
    """The clock blocks read time from: cache ages, progress cadence, HTTP back-off.

    A test's clock (:func:`installed_clock`) wins, in every thread and over
    every run, so it moves all three wherever the test installs it.
    Otherwise it is the current run's (a worker started with a copied
    context, as ``bounded_map`` starts them, sees the run), else a plain one.
    """
    installed = _installed[0]
    if installed is not None:
        return installed
    run = _RUN.get()
    return run.clock if run is not None else _PLAIN


@contextmanager
def installed_clock(clock: Clock) -> Iterator[Clock]:
    """Make ``clock`` what every block reads for the block inside (a test's), then restore."""
    previous, _installed[0] = _installed[0], clock
    try:
        yield clock
    finally:
        _installed[0] = previous


@contextmanager
def run_scope(run: Run) -> Iterator[Run]:
    """Make ``run`` the current run for the block inside, then restore the one before."""
    token = _RUN.set(run)
    try:
        yield run
    finally:
        _RUN.reset(token)


def note_preview() -> None:
    """Note on the current run, if any, that its command only previews."""
    run = _RUN.get()
    if run is not None:
        run.notes.preview = True


def fixed_rendering() -> Rendering | None:
    """The current run's fixed rendering; ``None`` outside a run or before it is fixed."""
    run = _RUN.get()
    return None if run is None else run.rendering


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
        from untaped.sdk._home import PLUGINS_FOLDER  # noqa: PLC0415 - errors imports the run

        return self.home / PLUGINS_FOLDER / self.name

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
    ``plugin`` is the command's own plugin; ``plugins`` is every plugin
    this install has, as ``untaped plugin list`` shows them.
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
    def plugins(self) -> tuple[InstalledPlugin, ...]:
        return self.run.composition.installed()

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
