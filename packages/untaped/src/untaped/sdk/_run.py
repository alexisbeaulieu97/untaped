"""The run: the per-invocation state a block reaches, and the handles built from it.

A block needs a few things only a run knows: the home folder, which plugin
owns which settings model, the settings the run picked, the stderr sink, the
terminal, the current project and the time. ``untaped.runtime`` builds one
:class:`Run` per invocation and sets it with :func:`run_scope`; the SDK reads
it with :func:`current_run` and never imports the runtime. The module is
private: a command sees the run only through ``self.ctx`` (:class:`Context`,
whose handles never include the run itself) and ``self.settings``; code
outside a command uses :func:`settings_of` and :func:`plugin_of` (exported
by ``untaped.sdk``), each naming a plugin by its settings model. The testing
kit's hooks live here too (:func:`installed_clock`, :func:`recording`).
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from rich.text import Text

    from untaped.sdk._terminal import Terminal
    from untaped.sdk._theme import ThemeSpec
    from untaped.sdk._ui import Ui
    from untaped.sdk.diagnostic import Diagnostic, Failure
    from untaped.sdk.errors import UntapedError
    from untaped.sdk.network.http import HttpFactory
    from untaped.sdk.plugin import SpelledCommand
    from untaped.sdk.plugin_records import InstalledPlugin
    from untaped.sdk.progress import ProgressHandle
    from untaped.sdk.projects import Project


class NoRunError(RuntimeError):
    """Code that needs the run was called outside one: a bug, never a user's mistake."""


class Diag(Protocol):
    """The stderr handle (``ctx.diag``): one method per level, written to the run's sink."""

    def emit(self, diagnostic: Diagnostic) -> None: ...

    def info(
        self,
        text: str | Text | Sequence[BaseModel],
        *,
        hint: str | None = None,
        unmuted: bool = False,
    ) -> None: ...

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


def _no_remover(plugin: str, rows: str, key: str) -> str | None:
    """No command removes a row in a run that composed no declaration."""
    return None


#: The attribute core's section class of a plugin's settings model holds that model under.
SECTION_OF = "__untaped_section_of__"


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
    #: The command line removing a row (plugin, rows, key), from the command marked ``removes =``.
    remove_line: Callable[[str, str, str], str | None] = _no_remover
    #: Each composed record kind, by its model's ``module:Qualname``.
    kinds: Mapping[str, str] = field(default_factory=dict)

    def owner_of_settings(self, model: type[BaseModel]) -> str:
        """The plugin whose settings model ``model`` is; :class:`LookupError` when none is.

        ``model`` may be the class a run's settings are loaded as, which core
        derives from the plugin's model to add the keys it injects (and marks
        with that model); a subclass of its own is no plugin's settings.
        """
        declared = model.__dict__.get(SECTION_OF, model)
        if (owner := self.owners.get(declared)) is not None:
            return owner
        raise LookupError(f"{model.__qualname__} is the settings model of no composed plugin")

    def kind_of(self, model: type) -> str | None:
        """The kind composition gave ``model`` (``None``: no composed plugin reaches it)."""
        return self.kinds.get(f"{model.__module__}:{model.__qualname__}")

    def model_of(self, kind: str) -> type | None:
        """The model composition gave ``kind`` (``None``: no composed plugin declares it)."""
        target = next((each for each, given in self.kinds.items() if given == kind), None)
        if target is None:
            return None
        module, _, qualname = target.partition(":")
        try:
            found: object = importlib.import_module(module)
            for part in qualname.split("."):
                found = getattr(found, part)
        except ImportError, AttributeError:  # a class in a function, a module gone
            return None
        return found if isinstance(found, type) else None

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

    The run decides it from what the line names (:attr:`Run.rendering`),
    and the sink renders by it, so a plugin has no reason to read it:
    branching on JSON around the sink would split what one line says.
    ``format`` is the ``--format`` the user chose (``None``: the command's
    own default), ``source`` where it came from (``--format``,
    ``UNTAPED_FORMAT`` or ``untaped.ui.format``), ``theme`` the
    ``untaped.ui.theme`` text messages render with (``None``: the default
    theme, as under JSON diagnostics).
    """

    format: str | None = None
    source: str | None = None
    theme: ThemeSpec | None = None


#: Output formats whose stderr diagnostics default to JSON Lines.
STRUCTURED_FORMATS = frozenset({"json", "yaml", "pipe"})


@dataclass(frozen=True)
class OutputPolicy:
    """What the run asked of its output (:attr:`Run.policy`); never in ``untaped.sdk``.

    The sink follows it, so a plugin has no reason to read it: branching on
    JSON around the sink would split what one line says. ``format`` and
    ``theme`` are the run's rendering once it is fixed (:meth:`Run.render_with`);
    ``forced`` is the ``UNTAPED_DIAGNOSTICS`` choice read when the run was
    built (``None``: follow the format); ``quiet``, ``verbose`` and
    ``deprecated`` are the root options ``--quiet``, ``--verbose`` and
    ``--deprecated`` (help lists deprecated things too), which the token
    pass writes onto the run once it started. ``theme`` is left out of the
    hash, since a ``ThemeSpec`` has none.
    """

    format: str | None = None
    forced: Literal["text", "json"] | None = None
    quiet: bool = False
    verbose: bool = False
    deprecated: bool = False
    theme: ThemeSpec | None = field(default=None, hash=False)

    @property
    def diagnostics(self) -> Literal["text", "json"]:
        """Whether stderr carries text or JSON Lines."""
        if self.forced is not None:
            return self.forced
        return "json" if self.format in STRUCTURED_FORMATS else "text"


@dataclass
class PolicyCell:
    """Where a run keeps its :class:`OutputPolicy`, held by reference.

    A child run for a nested pick holds its parent's cell, as it shares
    the parent's output, so a write under either is the one both read and
    the sink follows; a nested invocation, with an output of its own, gets
    a cell of its own.
    """

    policy: OutputPolicy = field(default_factory=OutputPolicy)


@dataclass(frozen=True)
class SettingsOverlay:
    """Candidate values for one section of one profile, laid over what a run loads.

    A ``None`` value means "unset this key" (a command token source removes
    the ``token``). Internal: ``setup`` checks a plugin against what the
    user typed, in a child run carrying it, before anything is written.
    """

    profile: str
    section: str
    values: Mapping[str, object]


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
    terminal: Callable[[], Ui]
    #: What every block reads time from (:func:`run_clock`).
    clock: Clock = field(default_factory=Clock)
    #: What blocks noted during ``run``, for core to read after it.
    notes: RunNotes = field(default_factory=RunNotes)
    #: How stderr renders: fixed once by the runtime (:meth:`render_with`) for
    #: a declared command or a line naming none; ``None`` until then.
    rendering: Rendering | None = None
    #: The profiles ``--profile`` picks for this run, in order (``()``: none):
    #: the root option's, written by the token pass (:meth:`set_profiles`), or
    #: a child run's own (``untaped.runtime.run.child_run``).
    profiles: tuple[str, ...] = ()
    #: Candidate settings a child run lays over what it loads; nothing is written.
    overlay: SettingsOverlay | None = None
    #: Where the run keeps what it asks of stderr (:attr:`policy`), which the
    #: sink of its ``diag`` follows: a child run sharing its parent's output
    #: holds its parent's cell, so the two never disagree.
    policy_cell: PolicyCell = field(default_factory=PolicyCell, repr=False)

    @property
    def home(self) -> Path:
        """The home folder; raises the error that keeps it from resolving."""
        return self.locate_home()

    @property
    def policy(self) -> OutputPolicy:
        """What the run asks of stderr, which the sink of its ``diag`` follows."""
        return self.policy_cell.policy

    @property
    def http(self) -> HttpFactory:
        """HTTP clients for any composed plugin's services, outside a command too (doctor)."""
        from untaped.sdk.network.http import HttpFactory  # noqa: PLC0415 - httpx's cost

        return HttpFactory(self.composition.owner_of_settings)

    @property
    def attached(self) -> bool:
        """Whether the run's composition is attached (:meth:`attach`)."""
        return self.composition is not _UNCOMPOSED

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
        self.set_policy(replace(self.policy, format=rendering.format, theme=rendering.theme))
        return self

    def set_policy(self, policy: OutputPolicy) -> None:
        """Replace what the run asks of stderr.

        The token pass writes the root flags onto the run once it started,
        and a line read with no declaration kept is read again once the root
        is composed, writing the ones it takes then onto the same run. A
        child run sharing its parent's output writes the policy it shares
        with the parent.
        """
        self.policy_cell.policy = policy

    def set_profiles(self, profiles: tuple[str, ...]) -> None:
        """Replace the run's ``--profile`` pick (:attr:`profiles`).

        As :meth:`set_policy`: the token pass writes the root ``--profile``
        onto the run once it started, before it is composed, and a line read
        with no declaration kept writes the ones it takes late onto the same
        run, whose sections are read under the pick it holds when read.
        Frozen to every other writer.
        """
        object.__setattr__(self, "profiles", profiles)


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


#: Where a test records the diagnostics shown (:func:`recording`), read as each run is built.
_recorder: ContextVar[list[Diagnostic] | None] = ContextVar("untaped_recorder", default=None)


@contextmanager
def recording() -> Iterator[list[Diagnostic]]:
    """Record every diagnostic shown by the runs built inside the block (a test's).

    The testing kit's hook behind ``Install.running()``'s ``diagnostics``.
    Unlike :func:`installed_clock` it is per context, so two threads or
    tasks recording at once each keep their own records (a worker started
    with a copied context records with the block that started it). A run
    built before the block keeps the recorder it was built with; the runs
    still write to stderr as they would (a tee), so captured text and the
    records agree. Nested, the inner block records alone until it ends.
    """
    captured: list[Diagnostic] = []
    token = _recorder.set(captured)
    try:
        yield captured
    finally:
        _recorder.reset(token)


def recorder() -> list[Diagnostic] | None:
    """The list a run built now records into (:func:`recording`); ``None`` outside one."""
    return _recorder.get()


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


def is_quiet() -> bool:
    """Whether the current run asked for ``--quiet``; ``False`` outside a run."""
    run = _RUN.get()
    return run is not None and run.policy.quiet


def is_verbose() -> bool:
    """Whether the current run asked for ``--verbose``; ``False`` outside a run."""
    run = _RUN.get()
    return run is not None and run.policy.verbose


def current_overlay() -> SettingsOverlay | None:
    """The current run's settings overlay; ``None`` outside a run or without one."""
    run = _RUN.get()
    return None if run is None else run.overlay


def run_or_none() -> Run | None:
    """The current run; ``None`` outside one (a bare thread, code no invocation runs)."""
    return _RUN.get()


def current_run() -> Run:
    """The current run; :class:`NoRunError` outside one."""
    run = _RUN.get()
    if run is None:
        raise NoRunError(
            "no untaped run is active: the runtime sets one per invocation, and a test "
            "runs the code under the testing kit's install(...).running()"
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


def http_of(model: type[BaseModel]) -> HttpFactory:
    """The HTTP clients of the plugin whose settings model is ``model``, wherever the code runs.

    What its ``self.ctx.http`` is, for its own code another plugin's command
    calls (git's repo store asks it for ``proxy_for``).
    """
    run = current_run()
    run.composition.owner_of_settings(model)  # a model no composed plugin owns raises
    return run.http


def now() -> datetime:
    """The run's wall time, in UTC: the one clock a plugin reads.

    The kit's ``clock`` moves it, with core's own stamps, cache ages and
    back-offs. Outside a run it raises :class:`NoRunError`, as
    :func:`settings_of` does.
    """
    current_run()
    return run_clock().now()


def settings_of[M: BaseModel](model: type[M]) -> M:
    """The settings of the plugin whose model is ``model``, as this run picked them."""
    run = current_run()
    section = run.picks(run.composition.owner_of_settings(model), model)
    if not isinstance(section, model):
        raise TypeError(f"the run picked {type(section).__qualname__} for {model.__qualname__}")
    return section


@dataclass(frozen=True)
class ProviderContext:
    """What a provider reaches without declaring it: ``self.ctx`` on a ``Provider``.

    ``diag`` writes to stderr; ``http`` builds HTTP clients; ``project``
    is the current project or ``None``; ``plugin`` is the providing plugin;
    ``plugins`` is every plugin this install has. No terminal: a provider runs inside someone
    else's command, so only the command asks.
    """

    _run: Run = field(repr=False)
    plugin: PluginHandle

    @property
    def diag(self) -> Diag:
        return self._run.diag

    @property
    def http(self) -> HttpFactory:
        """HTTP clients for this plugin's services: ``self.ctx.http.client(self.settings, ...)``."""
        return self._run.http

    @property
    def plugins(self) -> tuple[InstalledPlugin, ...]:
        return self._run.composition.installed()

    @property
    def project(self) -> Project | None:
        from untaped.sdk.projects import project_at  # noqa: PLC0415 - projects' cost

        return project_at(Path.cwd())


@dataclass(frozen=True)
class Context(ProviderContext):
    """What a command reaches without declaring it: ``self.ctx``.

    A provider's context (``diag``, ``http``, ``project``, ``plugin`` its
    own plugin, ``plugins``) and ``terminal``, the terminal handle for prompts and
    screens. The run they read is kept private: a plugin never reaches the
    sink's policy or the composition except through these handles.
    """

    @property
    def terminal(self) -> Terminal:
        return self._run.terminal()


def provider_context(plugin: str) -> ProviderContext:
    """The :class:`ProviderContext` of a provider of ``plugin``, from the current run."""
    run = current_run()
    return ProviderContext(_run=run, plugin=PluginHandle(name=plugin, home=run.home))


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
    return Context(_run=run, plugin=PluginHandle(name=name, home=run.home))
