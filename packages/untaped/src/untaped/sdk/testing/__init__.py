"""Testing helpers for driving Cyclopts command apps with captured output.

:func:`check_plugin` returns the rows of the checks only the composed root can
answer, for a linter to judge; its ``candidates`` argument composes a plugin
passed in directly instead of one discovered through entry points.
:func:`invoke_root` runs ``untaped ...`` in-process against the installed
plugins. :func:`compose_with`, :func:`assert_fills` and :func:`piped_through`
test contracts (``untaped.sdk.testing.contracts``); :func:`assert_surface` guards a plugin's
public surface and :func:`check` runs its plugin check (``untaped.sdk.testing.guards``).

Blocks read time from one clock (``untaped.sdk._run.run_clock``): cache
ages, progress cadence and HTTP back-off. Under :func:`manual_clock` it is a
:class:`ManualClock`, which moves only on ``advance(...)`` (``advance(hours=1)``
is "a rerun an hour later") and on a block's sleep, which never waits.

:func:`cache_clock` and :func:`progress_clock` are the older clocks, each
for one kind of time: :func:`cache_clock` for the wall time caches read,
:func:`progress_clock` for the progress cadence alone. :func:`invoke_cli`
freezes the cadence for the invocation unless the test set it, so
``CliResult.diagnostics`` carries no ``untaped.progress`` line however
slow the run (``--verbose`` still prints every update); a deadline or a
back-off inside keeps its time.

:meth:`Install.running` runs a block under an install's run, for a test
that calls a block directly; what it yields keeps the typed lines shown
inside (``run.diagnostics``), as ``invoke_cli`` fills ``CliResult.diagnostics``.

:func:`write_reference` writes another install's reference in
core's ``refs`` folder, so a test can say what holds a store's layouts.
:func:`make_project` makes a folder a project in the hermetic home, as
``untaped project init`` does, holding the items it is given.
:func:`release_ask`, :func:`move_ask` and :func:`plan_release` let a plugin
unit-test its item store's ``release`` and ``moved`` without core's holder
kinds.

A :class:`MemoryStore` stands in for this machine's password store:
``untaped.sdk.testing.plugin`` installs a fresh one for every test, so
``untaped auth login`` and ``Credential.store`` file secrets in memory and
``Credential.get`` reads them back; :func:`memory_store` returns it, and
installs one around a block of a suite that doesn't load the plugin.
"""

from __future__ import annotations

import copy
import io
import sys
import threading
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from cyclopts import App
from rich.console import Console

from untaped.core.commands.records import PluginCheck
from untaped.runtime.registry import PluginCandidate
from untaped.runtime.sink import cadence_clock, cadence_is_set, frozen_cadence, tick
from untaped.sdk._cli import run_cyclopts_app
from untaped.sdk._run import (
    _RUN,
    Clock,
    _installed,
    installed_clock,
    run_clock,
)
from untaped.sdk._run import recording as _recording
from untaped.sdk.errors import ConfigError
from untaped.sdk.marks import apply_marks, experimental
from untaped.sdk.projects import Project, holder_of, init
from untaped.sdk.prompt.prompts import (
    PromptBackend,
    reset_prompt_backend_override,
    reset_terminal_override,
    set_prompt_backend_override,
    set_terminal_override,
)
from untaped.sdk.storage._refs import PROJECT, AnchorId, RefHolder, RefTarget
from untaped.sdk.storage._refs import write_reference as _write_reference
from untaped.sdk.storage.cache import Cache, versioned
from untaped.sdk.storage.holds import (
    HolderState,
    HolderView,
    ItemStore,
    LetGo,
    MoveAsk,
    Refused,
    ReleaseAsk,
    _rewrite,
    holder_view,
)
from untaped.sdk.storage.holds import release_ask as _release_ask
from untaped.sdk.storage.state import StateRows
from untaped.sdk.testing.contracts import assert_fills, compose_with, piped_through
from untaped.sdk.testing.fixtures import MemoryCredentials, ScriptedTerminal
from untaped.sdk.testing.schema import assert_schema_pinned
from untaped.sdk.testing.screens import ScreenKeys, ScreenRun, drive_screen

if TYPE_CHECKING:
    from untaped.screen.picker import PickResult
    from untaped.sdk.diagnostic import Diagnostic
    from untaped.sdk.plugin import Plugin

__all__ = [
    "CacheClock",
    "CliInvoker",
    "CliResult",
    "Home",
    "Install",
    "ManualClock",
    "MemoryCredentials",
    "MemoryStore",
    "PluginCandidate",
    "PluginCheck",
    "ProgressClock",
    "PromptBackend",
    "Ran",
    "Running",
    "ScreenKeys",
    "ScreenRun",
    "ScriptedPromptBackend",
    "ScriptedTerminal",
    "TtyStringIO",
    "assert_destructive_contract",
    "assert_fills",
    "assert_schema_pinned",
    "assert_surface",
    "cache_clock",
    "check",
    "check_plugin",
    "compose_with",
    "current_memory_store",
    "damage",
    "drive_screen",
    "install",
    "invoke_cli",
    "invoke_root",
    "keep_value",
    "make_project",
    "manual_clock",
    "memory_store",
    "move_ask",
    "piped_through",
    "plan_release",
    "plugin_candidate",
    "progress_clock",
    "read_rows",
    "release_ask",
    "write_reference",
    "write_rows",
]


class MemoryStore(MemoryCredentials):
    """A password store in memory, for tests: what ``auth login`` saves, by entry.

    The 11.x name of :class:`MemoryCredentials`, kept until each plugin's
    tests move onto the ``credentials`` fixture.
    """


@contextmanager
def memory_store(store: MemoryStore | None = None) -> Iterator[MemoryStore]:
    """Use ``store`` (a fresh one by default) as the password store for the block."""
    from untaped.sdk.network._credential_store import (  # noqa: PLC0415
        reset_test_store,
        set_test_store,
    )

    chosen = MemoryStore() if store is None else store
    token = set_test_store(chosen)
    try:
        yield chosen
    finally:
        reset_test_store(token)


def current_memory_store() -> MemoryStore:
    """The memory store this test runs with (``untaped.sdk.testing.plugin`` installs one)."""
    from untaped.sdk.network._credential_store import _TEST_STORE  # noqa: PLC0415

    store = _TEST_STORE.get()
    if not isinstance(store, MemoryStore):
        raise RuntimeError("no memory store is installed; use untaped.sdk.testing.memory_store()")
    return store


class ManualClock(Clock):
    """A clock that moves only when the test moves it.

    Wall and monotonic time move together: :meth:`advance` and a block's
    :meth:`sleep` (an HTTP back-off, say) move both, never wait, and then run
    one progress tick, so a due line prints now. ``start`` is the wall time
    it begins at (now, to the second, by default; naive is UTC).
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._wall = _utc(start)
        self._monotonic = 0.0
        self._lock = threading.Lock()  # parallel workers back off at once
        #: Each wait a block asked for, in seconds, in order.
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._monotonic

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.slept.append(seconds)
        self._move(seconds)

    def advance(self, by: timedelta | float = 0, /, **parts: float) -> None:
        """Move the clock on by ``by`` (seconds or a timedelta) plus ``parts`` (``hours=1``)."""
        step = by if isinstance(by, timedelta) else timedelta(seconds=by)
        self._move((step + timedelta(**parts)).total_seconds())

    def _move(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError(f"a clock never goes back; got {seconds} seconds")
        with self._lock:
            self._wall += timedelta(seconds=seconds)
            self._monotonic += seconds
        tick()


def _utc(start: datetime | None) -> datetime:
    """``start`` in UTC (naive is UTC, as everywhere in untaped); ``None`` is now, to the second."""
    if start is None:
        return datetime.now(UTC).replace(microsecond=0)
    return start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)


@contextmanager
def manual_clock(start: datetime | None = None) -> Iterator[ManualClock]:
    """Run the block, and any run inside it, on a :class:`ManualClock`."""
    clock = ManualClock(start)
    with installed_clock(clock):
        yield clock


class _WallOnly(Clock):
    """A clock that answers wall time itself and monotonic time from the clock below it.

    A block's wait moves the wall clock and never waits: a test never sleeps.
    """

    def __init__(self, below: Clock, wall: CacheClock) -> None:
        self._below, self._wall = below, wall

    def now(self) -> datetime:
        return self._wall.now

    def monotonic(self) -> float:
        return self._below.monotonic()

    def sleep(self, seconds: float) -> None:
        self._wall.advance(timedelta(seconds=seconds))


def _cadence_is_the_tests() -> bool:
    """Whether the test already set what progress time reads (a manual clock, a progress clock)."""
    clock = _installed[0]
    while isinstance(clock, _WallOnly):
        clock = clock._below
    return cadence_is_set() or isinstance(clock, ManualClock)


class ProgressClock:
    """The progress clock of :func:`progress_clock`: time moves only when a test advances it."""

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock on, then run one progress tick, so a due line prints now."""
        self._now += seconds
        tick()


@contextmanager
def progress_clock() -> Iterator[ProgressClock]:
    """Run the block on a progress clock the test advances (``invoke_cli`` keeps it).

    Only the progress cadence reads it; a deadline or a back-off keeps its time.
    """
    clock = ProgressClock()
    with cadence_clock(clock):
        yield clock


class CacheClock:
    """The wall clock of :func:`cache_clock`: time moves only when a test advances it."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    @property
    def now(self) -> datetime:
        """The time a cache reads now."""
        return self._now

    def advance(self, by: timedelta) -> None:
        """Move the clock on by ``by``."""
        self._now += by


@contextmanager
def cache_clock(start: datetime | None = None) -> Iterator[CacheClock]:
    """Run the block with caches on a wall clock the test advances (``start``: now).

    Only wall time is the test's; monotonic time stays the clock's installed before.
    """
    clock = CacheClock(_utc(start))
    with installed_clock(_WallOnly(run_clock(), clock)):
        yield clock


@experimental
def keep_value[M](
    cache: Cache[M],
    key: str,
    value: M,
    *,
    refreshed_at: datetime,
    partition: str | None = None,
    version: str | None = None,
) -> None:
    """Keep ``value`` for ``key`` in a ``Cache(model=)`` as a fill at ``refreshed_at`` would.

    ``version`` is the version of the code that filled it (by default the
    cache's own now): another one's value is served outdated.
    """
    from untaped.sdk.storage.cache import (  # noqa: PLC0415 - the cache module is loaded already
        Freshness,
    )

    typing = cache._typing()
    if typing is None:
        raise ConfigError(f"keep_value: cache {cache.qualified} has no model= to keep a value of")
    filled_at = cache._version_now() if version is None else version
    paths = cache._paths(key, partition)
    paths.dir.mkdir(parents=True, exist_ok=True)
    cache._write_file(
        paths.file(versioned(typing, filled_at)),
        key,
        Freshness(refreshed_at=refreshed_at, version=filled_at),
        typing,
        typing.adapter.dump_json(value, by_alias=True, round_trip=True),
    )


@experimental
def damage(cache: Cache[Any] | StateRows[Any], key: str, *, partition: str | None = None) -> None:
    """Make ``key``'s file in ``cache`` unreadable, as a disk or an editor might.

    Given :class:`~untaped.sdk.storage.state.StateRows`, damages the row keyed ``key``
    instead (its key repeated, which no model can read).
    """
    if isinstance(cache, StateRows):
        name = cache._shape().key
        rows = [row for row in cache._file.read() if isinstance(row, dict) and row.get(name) == key]
        twice = (rows or [{name: key}])[:1] * 2
        cache._file.mutate(
            lambda current: [
                *(row for row in current if not (isinstance(row, dict) and row.get(name) == key)),
                *twice,
            ]
        )
        return
    typing = cache._typing()
    paths = cache._paths(key, partition)
    file = paths.file(None if typing is None else versioned(typing, cache._version_now()))
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("{not json\n", encoding="utf-8")


@experimental
def write_rows(rows: StateRows[Any], raw: Sequence[object]) -> None:
    """Write ``raw`` as ``rows``' rows, unchecked: a newer install's fields, a damaged row."""
    rows._file.mutate(lambda _: copy.deepcopy(list(raw)))


@experimental
def read_rows(rows: StateRows[Any]) -> list[Any]:
    """``rows``' rows as stored, unchecked."""
    return rows._file.read()


@dataclass(frozen=True)
class CliResult:
    """Captured CLI invocation result.

    ``diagnostics`` lists every stderr line the command emitted, as typed
    diagnostics (``Message``, ``Failure``, a plugin's own kinds…), in order,
    under text and JSON diagnostics alike: assert on kinds and fields
    instead of on wording.
    """

    exit_code: int
    stdout: str
    stderr: str
    exception: BaseException | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def output(self) -> str:
        """Combined stdout and stderr, matching Click's test result ergonomics."""
        return f"{self.stdout}{self.stderr}"


class CliInvoker:
    """Small test runner wrapper around :func:`invoke_cli`."""

    def invoke(
        self,
        command: App | Callable[..., Any],
        args: Iterable[str] | None = None,
        *,
        input: str | None = None,
        catch_exceptions: bool = True,
        interactive: bool = False,
        prompt_backend: PromptBackend | None = None,
        terminal: bool = False,
    ) -> CliResult:
        """Invoke a command with captured stdout/stderr."""
        return invoke_cli(
            command,
            args,
            input=input,
            catch_exceptions=catch_exceptions,
            interactive=interactive,
            prompt_backend=prompt_backend,
            terminal=terminal,
        )


def invoke_cli(
    command: App | Callable[..., Any],
    args: Iterable[str] | None = None,
    *,
    input: str | None = None,
    catch_exceptions: bool = True,
    interactive: bool = False,
    prompt_backend: PromptBackend | None = None,
    terminal: bool = False,
) -> CliResult:
    """Invoke a Cyclopts app or launcher while capturing terminal streams.

    A bare app is given the panels and help lines its marks ask for first, as
    the root would when mounting it (a nested sub-app computes the path of its
    replacement from its own name only). A mark on a ``PluginSpec`` is
    applied only by a composed root, so assert a spec-marked plugin's help
    through :func:`invoke_root`, not through its own app.

    Each invocation is its own: it runs under a child of the current run
    (an ``install(...).running()`` block's) with its own stderr output, so
    the failures it counts are its own, or under a bare run when none is
    current. A root app's invocation still starts a run of its own.

    ``interactive=True`` swaps stdin for a :class:`TtyStringIO` so TTY gates
    open; ``prompt_backend`` installs a scripted backend for the invocation
    (reaching even ``Terminal``s the command builds itself). The
    controlling terminal (``/dev/tty``, used for prompts while stdin is
    piped) is simulated: absent by default, present with ``terminal=True``
    (prompts then go to ``prompt_backend``). A command that runs a screen
    (``ui.run``) needs neither with a :class:`ScriptedPromptBackend`, which
    never touches a terminal: ``prompt_backend=ScriptedPromptBackend(screens=[...])``
    is enough (and ``interactive=True`` only if the command checks for a TTY itself).
    """
    from untaped.runtime.run import invocation  # noqa: PLC0415

    stdout = io.StringIO()
    stderr = io.StringIO()
    previous_stdin = sys.stdin
    sys.stdin = TtyStringIO(input or "") if interactive else io.StringIO(input or "")
    token = set_prompt_backend_override(prompt_backend) if prompt_backend is not None else None
    terminal_token = set_terminal_override(TtyStringIO if terminal else no_terminal)
    try:
        frozen = nullcontext() if _cadence_is_the_tests() else frozen_cadence()
        with (
            frozen,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            _recording() as emitted,
            invocation(),
        ):
            try:
                _call_command(command, list(args or []), stdout=stdout, stderr=stderr)
            except SystemExit as exc:
                return CliResult(
                    exit_code=_exit_code(exc),
                    stdout=stdout.getvalue(),
                    stderr=stderr.getvalue(),
                    exception=exc,
                    diagnostics=tuple(emitted),
                )
            except BaseException as exc:
                if not catch_exceptions:
                    raise
                return CliResult(
                    exit_code=1,
                    stdout=stdout.getvalue(),
                    stderr=stderr.getvalue(),
                    exception=exc,
                    diagnostics=tuple(emitted),
                )
    finally:
        sys.stdin = previous_stdin
        reset_terminal_override(terminal_token)
        if token is not None:
            reset_prompt_backend_override(token)
    return CliResult(
        exit_code=0,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        diagnostics=tuple(emitted),
    )


def invoke_root(argv: Sequence[str], *, input: str | None = None) -> CliResult:
    """Run ``untaped *argv`` in-process, composed from the installed providers.

    The supported way for a plugin's tests to drive the real root: the root
    is composed through entry-point discovery, as the ``untaped`` script does,
    and ``argv`` is invoked on it with captured output. ``input`` is what is
    piped on stdin (another command's ``--format pipe`` output, say).
    """
    from untaped.runtime.bootstrap import build_root_app  # noqa: PLC0415

    return CliInvoker().invoke(build_root_app().meta, list(argv), input=input)


def no_terminal() -> TextIO:
    """Terminal opener for tests without a controlling terminal."""
    raise OSError("no controlling terminal (test harness)")


def assert_destructive_contract(
    command: App | Callable[..., Any],
    args: Sequence[str],
    *,
    assert_unchanged: Callable[[], None] | None = None,
) -> None:
    """Assert a destructive verb honours the SDK confirmation contract.

    Invokes ``command`` twice with the same ``args`` (which must target the
    verb *positionally* — not via ``--stdin``, whose data leg would starve on
    the interactive run):

    1. **Piped stdin, no ``--yes``** → must refuse with the standard
       ``requires --yes`` error and a nonzero exit.
    2. **Interactive stdin, scripted decline** → must prompt exactly one
       confirm and honour the decline (no crash beyond ``SystemExit``).

    ``assert_unchanged`` (recommended) runs after each leg to verify the
    destructive effect did not happen — the helper cannot know what "no
    effect" means for the tool.
    """
    piped = invoke_cli(command, list(args))
    assert piped.exit_code != 0, (
        f"piped stdin without --yes must refuse, got exit {piped.exit_code}; "
        f"stderr: {piped.stderr!r}"
    )
    # The refusal surfaces either as a rendered `error: ... requires --yes`
    # line (command wrapped in report_errors) or as a caught ConfigError on
    # the result (bare command body) — accept both shapes.
    combined = f"{piped.stderr}{piped.stdout}{piped.exception or ''}"
    assert "requires --yes" in combined, (
        f"expected the standard 'requires --yes' refusal, got: {combined!r}"
    )
    if assert_unchanged is not None:
        assert_unchanged()

    backend = ScriptedPromptBackend(confirms=[False])
    interactive = invoke_cli(command, list(args), interactive=True, prompt_backend=backend)
    assert backend.calls and backend.calls[0][0] == "confirm", (
        "interactive path must prompt for confirmation before acting"
    )
    assert interactive.exception is None or isinstance(interactive.exception, SystemExit), (
        f"decline must exit cleanly, got {interactive.exception!r}"
    )
    if assert_unchanged is not None:
        assert_unchanged()


@contextmanager
def _in_a_run() -> Iterator[None]:
    """The block under the current run, or a bare one: a helper writes where a command would."""
    from untaped.runtime.run import entered, start_run  # noqa: PLC0415

    if _RUN.get() is not None:
        yield
        return
    with entered(start_run()):
        yield


@experimental
def write_reference(
    holder: RefHolder,
    targets: Sequence[RefTarget],
    *,
    anchor: Path | None = None,
    anchor_id: AnchorId | None = None,
    moved_from: Sequence[Path] = (),
    plugin: str | None = None,
    version: str | None = None,
    plugins: Sequence[str] | None = None,
) -> Path:
    """Write another holder's reference in the hermetic home, as that holder would; its path.

    An ``anchor`` is live while it holds an ``untaped-*.dist-info`` folder,
    and a holder without one lives until its file is removed. ``anchor_id``
    records the anchor's filesystem identity (a project's), and
    ``moved_from`` where it was before each move not yet fully repaired;
    ``plugins`` names the plugins an install composes, as an install writes
    it (empty included); without it the key is left out, as a project's
    file, or an install's that doesn't say (prune then keeps every folder).
    """
    with _in_a_run():
        return _write_reference(
            holder,
            targets,
            anchor=anchor,
            anchor_id=anchor_id,
            moved_from=moved_from,
            plugin=plugin,
            version=version,
            plugins=plugins,
        )


@experimental
def make_project(
    path: Path, *, holds: Mapping[ItemStore[Any], Collection[object]] | None = None
) -> Project:
    """Make ``path`` a project (created when missing) recorded in the hermetic home.

    As ``untaped project init PATH`` does: ``.untaped/project.json``, its
    ``.gitignore`` and the home's reference, holding each store's items in
    ``holds`` (normalised by the store's item type; no composition needed).
    """
    with _in_a_run():
        project = init(path).project
        targets = tuple(
            store.target(item) for store, items in (holds or {}).items() for item in items
        )
        if targets:
            _rewrite(
                holder_of(project),
                lambda held: (*held, *(t for t in dict.fromkeys(targets) if t not in held)),
                None,
            )
    return project


@experimental
def release_ask(
    store: ItemStore[Any],
    item: object,
    *,
    holder: HolderState = "present",
    others: int | Sequence[HolderState] = 0,
    force: bool = False,
) -> ReleaseAsk[Any]:
    """A :class:`~untaped.sdk.ReleaseAsk` for ``item`` with made-up holders, for unit tests.

    ``holder`` is the state of the project letting go; ``others`` the number
    of other present holders, or each other holder's state.
    """
    states = ["present"] * others if isinstance(others, int) else list(others)
    return ReleaseAsk(
        item=store._parse(item),
        holder=_view(0, holder),
        others=tuple(_view(n, state) for n, state in enumerate(states, start=1)),  # type: ignore[arg-type]
        force=force,
    )


@experimental
def move_ask(store: ItemStore[Any], item: object, *, moved_from: Path) -> MoveAsk[Any]:
    """A :class:`~untaped.sdk.MoveAsk` for ``item``: a present project moved from ``moved_from``."""
    holder = _view(0, "present")
    if moved_from == holder.anchor:
        holder = holder.model_copy(update={"anchor": holder.anchor.with_name("moved-here")})
    return MoveAsk(
        item=store._parse(item),
        holder=holder,
        others=(),
        moved_from=moved_from,
    )


@experimental
def plan_release(project: Project, store: ItemStore[Any]) -> list[LetGo[Any] | Refused]:
    """What ``store``'s ``release`` plans for each of ``project``'s holds of it; never applies.

    Every other holder is read from the hermetic home, as core reads them.
    """
    with _in_a_run():
        seen = holder_view(holder_of(project))
        if not isinstance(seen, HolderView):
            raise ConfigError(f"project {project.name} has no readable record in this home")
        return [
            store.release(_release_ask(store, item, seen, force=False))
            for item in project.holds(store)
        ]


def _view(n: int, state: HolderState) -> HolderView:
    name = f"{n:012x}"
    return HolderView(
        holder=RefHolder(kind=PROJECT, name=name),
        label=f"project p{n}",
        anchor=Path("/projects") / f"p{n}",
        state=state,
    )


def plugin_candidate(plugin: type[Plugin]) -> PluginCandidate:
    """Plugin class ``plugin`` as a discovered candidate, for composing it without installing it.

    It is the candidate ``install`` composes: its entry point's when it is
    installed, else one named ``untaped-<name>``. Pass the result to
    ``check_plugin(..., candidates=[...])`` or
    ``untaped.runtime.bootstrap.build_root_app(candidates=[...])``.
    """
    from untaped.runtime.registry import discover_candidates  # noqa: PLC0415
    from untaped.sdk.testing.install import candidate_of  # noqa: PLC0415

    return candidate_of(plugin, None, discover_candidates())


def check_plugin(
    name: str, *, candidates: Sequence[PluginCandidate] | None = None
) -> list[PluginCheck]:
    """The rows of the checks only an installed, composed plugin can answer.

    Composes ``name`` hermetically, as :func:`check` does, and
    returns one :class:`PluginCheck` per check: the conventions that read
    the composed root (names, provides requirements, the help tree, guides
    and overview lines, stability marks, structure's class checks, the import
    boundary), then ``contracts``,
    ``registration``, ``conformance``, ``live``, ``fills`` and ``schema``,
    each where the plugin has something for it to read. Rows
    carry a check name and a ``status``, never a severity: a linter decides
    what each means. The conventions that read only the plugin's files are
    not run.
    ``candidates`` replaces entry-point discovery, so an uninstalled spec can
    be checked (:func:`plugin_candidate`). An unknown name is a
    :class:`~untaped.sdk.errors.UsageError`. Composing, it can't run inside a
    contract call.
    """
    from untaped.core.plugin_check.check import check_plugins  # noqa: PLC0415
    from untaped.runtime import bootstrap  # noqa: PLC0415 - the root is heavy; pay only on use
    from untaped.runtime.registry import discover_candidates  # noqa: PLC0415

    found = list(discover_candidates()) if candidates is None else list(candidates)
    with bootstrap.composed_with(found) as result:
        return check_plugins(result, found, name)


class TtyStringIO(io.StringIO):
    """A ``StringIO`` that claims to be a terminal.

    Used as the harness stdin for interactive-path tests: TTY checks
    (``Terminal``, ``batch_apply``) pass, while prompts are answered by a
    :class:`ScriptedPromptBackend` rather than a real terminal read.
    """

    def isatty(self) -> bool:
        return True


class ScriptedPromptBackend(ScriptedTerminal):
    """A :class:`~untaped.sdk.prompt.prompts.PromptBackend` answering from fixed queues.

    The 11.x spelling of :class:`ScriptedTerminal`, scripted all at once:
    each prompt method pops its next scripted answer and records
    ``(method, message)`` in ``calls``; an exhausted queue raises
    :class:`ConfigError` so a test fails cleanly instead of hanging. With
    ``interrupt=True`` every prompt raises :class:`PromptInterruptedError`,
    simulating Ctrl-C. ``screens`` answers :meth:`Terminal.run` as
    :meth:`ScriptedTerminal.screens` takes them. The backend never touches a
    terminal, so ``Terminal.run`` and ``Terminal.pick_many`` do not require
    one: a command test needs no ``terminal=True`` and no TTY stdin.
    """

    def __init__(
        self,
        *,
        confirms: Sequence[bool] = (),
        texts: Sequence[str] = (),
        secrets: Sequence[str] = (),
        selections: Sequence[Any] = (),
        picks: Sequence[PickResult | None] = (),
        screens: Sequence[object] = (),
        interrupt: bool = False,
    ) -> None:
        super().__init__()
        self.present = True
        self._secrets_typed = False  # a secret prompt reads only ``secrets``
        self._interrupt = interrupt
        self._confirms.extend(confirms)
        self._texts.extend(texts)
        self._secrets.extend(secrets)
        self._selections.extend(selections)
        self._picks.extend(picks)
        self._screens.extend(screens)

    @property
    def calls(self) -> list[tuple[str, str]]:
        """Every prompt asked, as ``(method, message)``: :attr:`ScriptedTerminal.asked`."""
        return self.asked


def _call_command(
    command: App | Callable[..., Any],
    args: list[str],
    *,
    stdout: io.StringIO,
    stderr: io.StringIO,
) -> None:
    console = _console(stdout)
    error_console = _console(stderr)
    if isinstance(command, App):
        target = command.meta if command.meta.default_command is not None else command
        if target is command and command._meta_parent is None:
            # A composed root (or its meta app) marked its commands when it
            # mounted them; a bare plugin app did not. Never resolves a
            # lazy plugin.
            apply_marks(command, path=() if command.name == ("untaped",) else command.name[:1])
        run_cyclopts_app(
            target,
            args,
            console=console,
            error_console=error_console,
            result_action="print_non_int_sys_exit",
        )
        return
    command(args, console=console, error_console=error_console)


def _console(stream: io.StringIO) -> Console:
    return Console(
        file=stream,
        force_terminal=False,
        color_system=None,
        width=120,
    )


def _exit_code(exc: SystemExit) -> int:
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


# Last: these read this module's runners.
from untaped.sdk.testing.guards import assert_surface, check  # noqa: E402
from untaped.sdk.testing.install import Home, Install, Ran, Running, install  # noqa: E402
