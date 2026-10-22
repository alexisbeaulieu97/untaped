"""Testing helpers for driving Cyclopts command apps with captured output.

:func:`check_conventions` checks one installed plugin against
``docs/reference/conventions.md``; its ``candidates`` argument composes a plugin
passed in directly instead of one discovered through entry points.
:func:`check_plugin` returns the rows of the checks only the composed root can
answer, for a linter to judge.
:func:`invoke_root` runs ``untaped ...`` in-process against the installed
plugins. :func:`compose_with` and :func:`assert_fills` test contracts
(``untaped.testing.contracts``); :func:`assert_surface` guards a plugin's
public surface (``untaped.testing.surface``).

Progress lines follow a clock, not the wall: :func:`invoke_cli` freezes the
progress clock for the invocation, so ``CliResult.diagnostics`` carries no
``untaped.progress`` line however slow the run (``--verbose`` still prints
every update). A test that wants the cadence runs under
:func:`progress_clock` and calls ``advance(seconds)`` from inside the
command (a fake the command calls, say).

A cache (``untaped.sdk.Cache``) reads its ages from the wall clock; under
:func:`cache_clock` it reads the test's instead, which moves only on
``advance(timedelta)``, so "a rerun an hour later" is one call. The two
clocks are independent.

:func:`write_reference` writes another install's reference in
``~/.untaped/refs/``, so a test can say what holds a store's layouts.
:func:`make_project` makes a folder a project in the hermetic home, as
``untaped project init`` does, holding the items it is given.
:func:`release_ask`, :func:`move_ask` and :func:`plan_release` let a plugin
unit-test its item store's ``release`` and ``moved`` without core's holder
kinds.

A :class:`MemoryStore` stands in for this machine's password store:
``untaped.testing.plugin`` installs a fresh one for every test, so
``untaped auth login`` and ``Credential.store`` file secrets in memory and
``Credential.get`` reads them back; :func:`memory_store` returns it, and
installs one around a block of a suite that doesn't load the plugin.
"""

from __future__ import annotations

import ast
import copy
import io
import sys
import warnings
from collections import deque
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, cast

from cyclopts import App
from rich.console import Console

from untaped.cache import Cache, installed_utcnow, versioned
from untaped.cli import run_cyclopts_app
from untaped.errors import ConfigError, PromptInterruptedError
from untaped.holds import (
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
from untaped.holds import release_ask as _release_ask
from untaped.management.records import PluginCheckRow
from untaped.output.sink import current_clock, installed_clock, recording, tick
from untaped.plugins.registry import PluginCandidate
from untaped.projects import Project, holder_of, init
from untaped.prompts import (
    PromptBackend,
    PromptChoice,
    reset_prompt_backend_override,
    reset_terminal_override,
    set_prompt_backend_override,
    set_terminal_override,
)
from untaped.refs import PROJECT, RefHolder, write_reference
from untaped.screen.core import Cancel, Quit, Screen
from untaped.stability import apply_marks, deprecated, experimental
from untaped.state import StateRows
from untaped.testing.contracts import assert_fills, compose_with
from untaped.testing.schema import assert_schema_pinned
from untaped.testing.screens import ScreenKeys, ScreenRun, drive_screen
from untaped.testing.surface import assert_surface

if TYPE_CHECKING:
    from untaped.output.diagnostic import Diagnostic
    from untaped.picker import PickRequest, PickResult
    from untaped.plugins.registry import PluginSpec
    from untaped.theme import ThemeSpec

__all__ = [
    "CacheClock",
    "CliInvoker",
    "CliResult",
    "MemoryStore",
    "PluginCandidate",
    "PluginCheckRow",
    "ProgressClock",
    "PromptBackend",
    "ScreenKeys",
    "ScreenRun",
    "ScriptedPromptBackend",
    "TtyStringIO",
    "allowed_names",
    "allows",
    "assert_destructive_contract",
    "assert_fills",
    "assert_schema_pinned",
    "assert_surface",
    "cache_clock",
    "check_conventions",
    "check_plugin",
    "compose_with",
    "current_memory_store",
    "damage",
    "drive_screen",
    "import_targets",
    "invoke_cli",
    "invoke_root",
    "keep_value",
    "make_project",
    "memory_store",
    "move_ask",
    "plan_release",
    "plugin_candidate",
    "progress_clock",
    "read_rows",
    "release_ask",
    "write_reference",
    "write_rows",
]


class MemoryStore:
    """A password store in memory, for tests: what ``auth login`` saves, by entry.

    ``entries`` maps each entry to its secret and ``labels`` to how a real
    store would list it, so a test can check both.
    """

    name = "memory"

    def __init__(self) -> None:
        self.entries: dict[str, str] = {}
        self.labels: dict[str, str | None] = {}

    def preflight(self) -> None:
        """A memory store always works."""

    def save(self, entry: str, token: str, *, label: str | None = None) -> None:
        self.entries[entry] = token
        self.labels[entry] = label

    def read(self, entry: str) -> str:
        if entry not in self.entries:
            raise ConfigError(f"the in-memory store has no entry {entry}")
        return self.entries[entry]

    def delete(self, entry: str) -> None:
        if entry not in self.entries:
            raise ConfigError(f"the in-memory store has no entry {entry}")
        del self.entries[entry]
        self.labels.pop(entry, None)

    def gone(self, entry: str) -> bool:
        return entry not in self.entries

    def describe(self, entry: str) -> str:
        return f"memory ({entry})"


@contextmanager
def memory_store(store: MemoryStore | None = None) -> Iterator[MemoryStore]:
    """Use ``store`` (a fresh one by default) as the password store for the block."""
    from untaped.credential_store import reset_test_store, set_test_store  # noqa: PLC0415

    chosen = MemoryStore() if store is None else store
    token = set_test_store(chosen)
    try:
        yield chosen
    finally:
        reset_test_store(token)


def current_memory_store() -> MemoryStore:
    """The memory store this test runs with (``untaped.testing.plugin`` installs one)."""
    from untaped.credential_store import _TEST_STORE  # noqa: PLC0415

    store = _TEST_STORE.get()
    if not isinstance(store, MemoryStore):
        raise RuntimeError("no memory store is installed; use untaped.testing.memory_store()")
    return store


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
    """Run the block on a progress clock the test advances (``invoke_cli`` keeps it)."""
    clock = ProgressClock()
    with installed_clock(clock):
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
    """Run the block with caches on a wall clock the test advances (``start``: now)."""
    if start is None:
        start = datetime.now(UTC).replace(microsecond=0)
    elif start.tzinfo is None:
        start = start.replace(tzinfo=UTC)  # naive is UTC, as everywhere in untaped
    clock = CacheClock(start)
    with installed_utcnow(clock):
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
    from untaped.cache import Freshness  # noqa: PLC0415 - the cache module is loaded already

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

    Given :class:`~untaped.state.StateRows`, damages the row keyed ``key``
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

    ``interactive=True`` swaps stdin for a :class:`TtyStringIO` so TTY gates
    open; ``prompt_backend`` installs a scripted backend for the invocation
    (reaching even ``UiContext``s the command builds itself). The
    controlling terminal (``/dev/tty``, used for prompts while stdin is
    piped) is simulated: absent by default, present with ``terminal=True``
    (prompts then go to ``prompt_backend``). A command that runs a screen
    (``ui.run``) needs neither with a :class:`ScriptedPromptBackend`, which
    never touches a terminal: ``prompt_backend=ScriptedPromptBackend(screens=[...])``
    is enough (and ``interactive=True`` only if the command checks for a TTY itself).
    """
    stdout = io.StringIO()
    stderr = io.StringIO()
    previous_stdin = sys.stdin
    sys.stdin = TtyStringIO(input or "") if interactive else io.StringIO(input or "")
    token = set_prompt_backend_override(prompt_backend) if prompt_backend is not None else None
    terminal_token = set_terminal_override(TtyStringIO if terminal else no_terminal)
    try:
        frozen = nullcontext() if isinstance(current_clock(), ProgressClock) else progress_clock()
        with (
            frozen,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            recording() as emitted,
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
    from untaped.bootstrap import build_root_app  # noqa: PLC0415

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


@experimental
def make_project(
    path: Path, *, holds: Mapping[ItemStore[Any], Collection[object]] | None = None
) -> Project:
    """Make ``path`` a project (created when missing) recorded in the hermetic home.

    As ``untaped project init PATH`` does: ``.untaped/project.json``, its
    ``.gitignore`` and the home's reference, holding each store's items in
    ``holds`` (normalised by the store's item type; no composition needed).
    """
    project = init(path).project
    targets = tuple(store.target(item) for store, items in (holds or {}).items() for item in items)
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
    seen = holder_view(holder_of(project))
    if not isinstance(seen, HolderView):
        raise ConfigError(f"project {project.name} has no readable record in this home")
    return [
        store.release(_release_ask(store, item, seen, force=False)) for item in project.holds(store)
    ]


def _view(n: int, state: HolderState) -> HolderView:
    name = f"{n:012x}"
    return HolderView(
        holder=RefHolder(kind=PROJECT, name=name),
        label=f"project p{n}",
        anchor=Path("/projects") / f"p{n}",
        state=state,
    )


def plugin_candidate(
    spec: PluginSpec,
    *,
    distribution: str | None = None,
    requires_dist: Sequence[str] = (),
) -> PluginCandidate:
    """``spec`` as a discovered candidate, for composing it without installing it.

    ``distribution`` defaults to ``untaped-<name>``, the name the
    ``plugin-name`` convention expects; ``requires_dist`` stands for the
    distribution's ``Requires-Dist`` lines (a provider's owner requirement).

    Pass the result to ``check_conventions(..., candidates=[...])`` or
    ``untaped.bootstrap.build_root_app(candidates=[...])``.
    """
    return PluginCandidate(
        distribution=distribution or f"untaped-{spec.name}",
        name=spec.name,
        target=spec,
        requires_dist=tuple(requires_dist),
    )


def check_plugin(
    name: str, *, candidates: Sequence[PluginCandidate] | None = None
) -> list[PluginCheckRow]:
    """The rows of the checks only an installed, composed plugin can answer.

    Composes ``name`` hermetically, as ``untaped plugin check`` does, and
    returns one :class:`PluginCheckRow` per check: the conventions that read
    the composed root (names, provides requirements, the help tree, guides
    and map lines, stability marks, structure's class checks, the import
    boundary), then ``contracts``,
    ``registration``, ``conformance``, ``live``, ``fills``, ``schema`` and
    ``migrations``, each where the plugin has something for it to read. Rows
    carry a check name and a ``status``, never a severity: a linter decides
    what each means. The conventions that read only the plugin's files are
    not run.
    ``candidates`` replaces entry-point discovery, so an uninstalled spec can
    be checked (:func:`plugin_candidate`). An unknown name is a
    :class:`~untaped.errors.UsageError`. Composing, it can't run inside a
    contract call.
    """
    from untaped import bootstrap  # noqa: PLC0415 - the root is heavy; pay only on use
    from untaped.management.plugin_check import check_plugins  # noqa: PLC0415
    from untaped.plugins.registry import discover_candidates  # noqa: PLC0415

    found = list(discover_candidates()) if candidates is None else list(candidates)
    with bootstrap.composed_with(found, quiet=True) as result:
        return check_plugins(result, found, name, core_only=True)


def allows(source_lines: list[str], lineno: int, rule: str) -> bool:
    """Whether line ``lineno`` (1-based) carries ``# untaped: allow`` for bare id ``rule``.

    The one reading of the marker every source rule shares: it names rules
    bare (``print``) or as ``lint.print``, comma-separated.
    """
    from untaped.conventions.allow import allowed  # noqa: PLC0415

    return allowed(source_lines, lineno, rule)


def allowed_names(line: str) -> frozenset[str]:
    """The rule names a source line's ``# untaped: allow`` marker names, as written.

    The same marker :func:`allows` reads, for a reader that resolves the
    names itself: ``lint`` honours ``house.no-print`` and a bare ``no-print``
    on a repository rule's findings.
    """
    from untaped.conventions.allow import allowed_names as names  # noqa: PLC0415

    return names(line)


def import_targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """The absolute modules an import statement imports, from a module of ``package``.

    ``from . import x`` binds the submodule; a relative import reaching above
    the top-level package resolves to nothing. The one import resolver every
    source rule shares.
    """
    from untaped.conventions.source import import_targets as resolve  # noqa: PLC0415

    return resolve(node, package)


# untaped's mark dates it on the surface; the literal under it is what mypy reports
# (``deprecated_message``'s wording for that mark).
@deprecated(removed_in=12, replacement="untaped_lint.testing.assert_clean from untaped[lint]")
@warnings.deprecated(
    "check_conventions is deprecated and will be removed in 12.0; "
    "use untaped_lint.testing.assert_clean from untaped[lint]"
)
def check_conventions(
    plugin: str,
    *,
    tests_dir: Path | None = None,
    candidates: Sequence[PluginCandidate] | None = None,
) -> None:
    """Fail with every convention violation of ``plugin``.

    The rules are in ``docs/reference/conventions.md#enforcement``.

    Checks the installed plugin's command subtree and its own source
    files: command grammar, stderr wording, package structure and layering.
    ``tests_dir`` adds the private-test-import check over those tests.
    ``candidates`` replaces entry-point discovery, so a test can compose a
    provider that is not installed. ``# untaped: allow <rule>`` on the flagged
    node's first line allows that one violation. A quarantined plugin
    fails with the reason it was refused, such as a broken rename declaration.
    The rules that read only the plugin's files run only when ``untaped[lint]``
    is installed, which now owns them; without it a warning says so.
    Composing, it brings back the composition it started with (and warns
    about no quarantined plugin), and can't run inside a contract call.
    """
    from untaped import bootstrap  # noqa: PLC0415 - the root is heavy; pay only on use
    from untaped.conventions import (  # noqa: PLC0415
        FILE_RULES_MISSING,
        file_rules_installed,
        plugin_violations,
    )

    if not file_rules_installed():
        warnings.warn(FILE_RULES_MISSING, UserWarning, stacklevel=3)  # past the deprecated wrapper

    with bootstrap.kept_composition(quiet=True):
        found = plugin_violations(plugin, tests_dir=tests_dir, candidates=candidates)
    assert not found, "convention violations:\n" + "\n".join(f"  {line}" for line in found)


class TtyStringIO(io.StringIO):
    """A ``StringIO`` that claims to be a terminal.

    Used as the harness stdin for interactive-path tests: TTY checks
    (``UiContext``, ``batch_apply``) pass, while prompts are answered by a
    :class:`ScriptedPromptBackend` rather than a real terminal read.
    """

    def isatty(self) -> bool:
        return True


class ScriptedPromptBackend:
    """A :class:`~untaped.prompts.PromptBackend` answering from fixed queues.

    Each prompt method pops its next scripted answer and records
    ``(method, message)`` in ``calls``; an exhausted queue raises
    :class:`ConfigError` so a test fails cleanly instead of hanging. With
    ``interrupt=True`` every prompt raises :class:`PromptInterruptedError`,
    simulating Ctrl-C.

    ``screens`` answers :meth:`UiContext.run`: each entry is the screen's
    result (returned as ``Quit(entry)``), a :class:`Quit` (its result is
    returned, like the screen's own), a :class:`Cancel` (or the class itself), an exception
    instance or class (raised), or :class:`ScreenKeys` (replayed through
    :func:`drive_screen` with commands run synchronously, so a command-level
    test can run a real screen). ``ran`` keeps the screens it was asked to
    run. The backend never touches a terminal, so ``UiContext.run`` and
    ``UiContext.pick_many`` do not require one: a command test needs no
    ``terminal=True`` and no TTY stdin.
    """

    needs_terminal = False

    def __init__(
        self,
        *,
        confirms: Sequence[bool] = (),
        texts: Sequence[str] = (),
        secrets: Sequence[str] = (),
        selections: Sequence[Any] = (),
        multiselects: Sequence[list[Any]] = (),
        picks: Sequence[PickResult | None] = (),
        screens: Sequence[object] = (),
        interrupt: bool = False,
    ) -> None:
        self._interrupt = interrupt
        self._confirms = deque(confirms)
        self._texts = deque(texts)
        self._secrets = deque(secrets)
        self._selections = deque(selections)
        self._multiselects = deque(multiselects)
        self._picks = deque(picks)
        self._screens = deque(screens)
        self.ran: list[Screen[Any, Any]] = []
        self.calls: list[tuple[str, str]] = []

    def _next(self, queue: deque[Any], method: str, message: str) -> Any:
        self.calls.append((method, message))
        if self._interrupt:
            raise PromptInterruptedError("prompt cancelled")
        if not queue:
            raise ConfigError(f"no scripted {method} answer for prompt {message!r}")
        return queue.popleft()

    def confirm(self, message: str, *, default: bool) -> bool:
        return bool(self._next(self._confirms, "confirm", message))

    def text(self, message: str, *, default: str | None) -> str:
        return str(self._next(self._texts, "text", message))

    def secret(self, message: str, *, confirmation: bool) -> str:
        return str(self._next(self._secrets, "secret", message))

    def select(
        self,
        message: str,
        choices: Sequence[PromptChoice[Any]],
        *,
        default: Any | None,
        search: bool,
    ) -> Any:
        return self._next(self._selections, "select", message)

    def multiselect(
        self,
        message: str,
        choices: Sequence[PromptChoice[Any]],
        *,
        defaults: Sequence[Any],
    ) -> list[Any]:
        return list(self._next(self._multiselects, "multiselect", message))

    def pick_many(self, request: PickRequest) -> PickResult | None:
        return cast("PickResult | None", self._next(self._picks, "pick_many", request.heading))

    def run_screen[M, R](self, screen: Screen[M, R], *, theme: ThemeSpec) -> Quit[R] | Cancel:
        self.ran.append(screen)
        entry = self._next(self._screens, "run_screen", screen.title)
        if isinstance(entry, BaseException) or (
            isinstance(entry, type) and issubclass(entry, BaseException)
        ):
            raise entry
        if entry is Cancel:
            return Cancel()
        if isinstance(entry, Quit | Cancel):
            return cast("Quit[R] | Cancel", entry)
        if isinstance(entry, ScreenKeys):
            run = drive_screen(screen, entry, theme=theme, commands="sync")
            if run.outcome is None:
                raise ConfigError(
                    f"the scripted keys did not end screen {screen.title!r}; "
                    "finish them with a key that quits"
                )
            return run.outcome
        return cast("Quit[R]", Quit(entry))


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
