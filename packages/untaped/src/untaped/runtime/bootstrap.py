"""Plugin composition root for the unified ``untaped`` shell.

Every plugin is discovered through the ``untaped.plugins``
entry-point group and validated before settings registration or app
mounting. Only plugins that survive validation
contribute command trees, settings sections, skills, or doctor checks.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from importlib import metadata
from itertools import chain
from typing import Annotated, Any

from cyclopts import App, Group, Parameter
from cyclopts.command_spec import CommandSpec
from cyclopts.core import _apply_parent_defaults_to_app

from untaped.core.commands.plugins import INSTALL_HINT
from untaped.core.commands.skills import check_installed_skills, composed_skills
from untaped.core.settings import NAME
from untaped.core.skills import PackagedSkill
from untaped.runtime.declaration import Declaration
from untaped.runtime.help_panels import GLOBAL_OPTIONS_GROUP, PLUGINS_GROUP
from untaped.runtime.mount import Dispatched, Mount, add_commands, dispatching, repairs_at
from untaped.runtime.registry import (
    CompositionResult,
    PluginCandidate,
    PluginSpec,
    QuarantineRecord,
    RegisteredPlugin,
    compose,
    discover_candidates,
    kept_declaration,
    run_deferred_factory,
    section_settings,
    spliced_state,
)
from untaped.runtime.root_options import (
    RootLine,
    _before_separator,
    _root_callback_signature,
    _root_options,
    _RootOption,
    applied_root_options,
    dispatch_line,
    resolve_command,
)
from untaped.runtime.settings import (
    register_profile_settings,
    register_state_settings,
    reset_config_registry,
)
from untaped.runtime.sink import derived_exit_code, exit_on_counted_failure
from untaped.sdk._cli import (
    apply_default_format,
    given_tokens,
    note_requested_format,
    report_errors,
    run_cyclopts_app,
)
from untaped.sdk._run import Run, current_run
from untaped.sdk.errors import ConfigError
from untaped.sdk.git import forward_signals
from untaped.sdk.marks import (
    apply_marks,
    bind_behaviours,
    bind_owners,
    mark_app,
    panel_for,
)
from untaped.sdk.network.credentials import bind_credentials
from untaped.sdk.storage._refs import refresh
from untaped.sdk.storage.cache import current_call


def _shell_app() -> App:
    return App(name=NAME, help="Unified untaped developer CLI.")


_COMPOSED_RESULT: CompositionResult | None = None
#: The root :func:`build_root_app` last built, which :func:`root_app` reads.
_BUILT_ROOT: App | None = None

#: The run :func:`run_root` started and the line its token pass left, until the
#: root callback takes them over (dispatcher state: an invocation nested in a
#: command starts its own).
_STARTED: ContextVar[tuple[Run, RootLine] | None] = ContextVar("untaped_started_run", default=None)


def _register_sections(result: CompositionResult) -> None:
    """Register core's and every composed plugin's settings sections.

    Runs after validation succeeds, so a plugin that fails any check
    registers nothing, and again whenever the registry is rebuilt for the
    remembered composition (:func:`_reregister`). Each plugin's distribution
    is bound as the owner its deprecations name, and its listed behaviours
    as its own (read only when one of them first warns).
    """
    bind_owners({plugin.spec.name: plugin.plugin_ref.distribution for plugin in result.plugins})
    bind_behaviours({plugin.spec.name: plugin.spec.deprecations for plugin in result.plugins})
    bind_credentials({plugin.spec.name: plugin.spec.credentials for plugin in result.plugins})
    for spec in (result.core, *(plugin.spec for plugin in result.plugins)):
        section = section_settings(spec)
        if section is not None:
            register_profile_settings(spec.name, section, spec.stability)
        spliced = spliced_state(spec)
        if spliced is not None:
            register_state_settings(spec.name, spliced)


def _attach(run: Run, result: CompositionResult) -> None:
    """Attach the root composition ``result`` to the invocation's ``run``, and warn.

    Only the root composition warns about its quarantined plugins, one
    warning each, through the run's sink: a composition nested in a command
    (a plugin check, the surface walk, the testing kit's) is never attached
    to a run, so it never warns again.
    """
    from untaped.runtime.run import composition_of  # noqa: PLC0415 - a start runs no command

    run.attach(composition_of(result.mount.declaration, result.mount.settings))
    for record in result.quarantine:
        run.diag.warning(
            f"plugin {record.name!r} from {record.distribution!r} quarantined "
            f"[{record.reason}]: {record.detail}"
        )


def compose_root(
    *,
    candidates: Sequence[PluginCandidate] | None = None,
) -> CompositionResult:
    """Discover, validate, and register one composition.

    Discovery (entry-point candidates, or ``candidates`` when given) runs BEFORE any
    settings registration or resolution; registration happens only after every
    surviving plugin validates. Remembers the composition for :func:`reset`.
    Warns about nothing: the root warns about its quarantined plugins once it
    attaches the composition to the invocation's run.

    Raises ``RuntimeError`` inside a contract call: the composition is
    process-global, so a provider composing would swap it under the providers
    ``gather`` runs beside it (``docs/contracts.md``).
    """
    global _COMPOSED_RESULT
    _refuse_inside_a_contract_call()
    candidates = discover_candidates() if candidates is None else candidates
    result = compose(candidates)
    _register_sections(result)
    _COMPOSED_RESULT = result
    return result


def _refuse_inside_a_contract_call() -> None:
    call = current_call()
    if call is not None:
        raise RuntimeError(
            f"{call.caller} composed the root inside a contract call; a contract method "
            "must not change process-global state such as the composition"
        )


@contextmanager
def kept_composition() -> Iterator[None]:
    """Bring back the composition (and its settings registrations) the block started with.

    The profile override is left as it is (a plugin's check runs under the
    user's ``--profile``), as are the run's root flags. Raises
    ``RuntimeError`` inside a contract call, as :func:`compose_root` does,
    since leaving the block re-registers the settings.
    """
    global _COMPOSED_RESULT, _BUILT_ROOT
    _refuse_inside_a_contract_call()
    previous, previous_root = _COMPOSED_RESULT, _BUILT_ROOT
    try:
        yield
    finally:
        _COMPOSED_RESULT, _BUILT_ROOT = previous, previous_root
        _reregister()


@contextmanager
def composed_with(candidates: Sequence[PluginCandidate]) -> Iterator[CompositionResult]:
    """Compose ``candidates`` for the block, then bring back the composition before it."""
    with kept_composition():
        yield compose_root(candidates=candidates)


def _reregister() -> None:
    reset_config_registry()
    if _COMPOSED_RESULT is not None:
        _register_sections(_COMPOSED_RESULT)
    else:
        bind_owners({})
        bind_behaviours({})
        bind_credentials({})


def composition() -> CompositionResult:
    """The composition the last :func:`compose_root` (or :func:`build_root_app`) remembered."""
    if _COMPOSED_RESULT is None:
        raise RuntimeError("nothing composed yet; call compose_root() or build_root_app() first")
    return _COMPOSED_RESULT


def reset() -> None:
    """Clear invocation-scoped state back to the just-composed composition.

    Clears the settings caches and the config registry, then re-registers
    the just-composed core and plugins. Exists for test isolation; never
    called implicitly between user invocations.
    """
    _reregister()


def _clear_for_tests() -> None:
    """Drop the remembered composition and root entirely (test isolation only)."""
    global _COMPOSED_RESULT, _BUILT_ROOT
    _COMPOSED_RESULT = None
    _BUILT_ROOT = None
    reset()


def _resolve_version() -> str:
    try:
        return metadata.version(NAME)
    except metadata.PackageNotFoundError as exc:
        raise ConfigError(
            f"shell {NAME!r} could not resolve version from distribution {NAME!r}"
        ) from exc


def build_root_app(
    *,
    candidates: Sequence[PluginCandidate] | None = None,
) -> App:
    """Compose core plus plugins and return the root app.

    Mounts core's commands (those on ``Core``'s class) at the root from the
    declaration and each validated plugin's sub-app under its plugin name,
    wires ``--version`` to installed-distribution metadata, installs
    position-independent root options, and registers shell completion. In
    tests, drive ``app.meta`` through :class:`~untaped.sdk.testing.CliInvoker` (or
    :func:`~untaped.sdk._cli.run_cyclopts_app`), never by calling it directly:
    the root reads the tokens as given only there. Run via :func:`run_root`
    in production.
    """
    candidates = list(candidates) if candidates is not None else list(discover_candidates())
    result = compose_root(candidates=candidates)
    root = _shell_app()
    root.meta.group_parameters = GLOBAL_OPTIONS_GROUP
    if not result.plugins and not result.quarantine:
        root.help = f"{root.help}\n\n{INSTALL_HINT}"
    _mount_core(root, result.mount)
    for plugin in result.plugins:
        _mount_plugin(root, plugin)
    root.version = _resolve_version
    root.config = (apply_default_format,)
    skills = composed_skills(result)
    _install_root_callback(
        root,
        _root_options(),
        result=result,
        after_command=lambda tokens, ran, failed: _check_skills_after(
            root, tokens, ran, skills, failed=failed
        ),
    )
    for flag in chain(root.help_flags, root.version_flags):
        root[flag].group = (GLOBAL_OPTIONS_GROUP,)
    root.register_install_completion_command(group=GLOBAL_OPTIONS_GROUP)
    global _BUILT_ROOT
    _BUILT_ROOT = root
    return root


def root_app() -> App:
    """The root :func:`build_root_app` last built, which ``untaped agents`` describes."""
    if _BUILT_ROOT is None:
        raise RuntimeError("no root app built yet; call build_root_app() first")
    return _BUILT_ROOT


def root_command(token: str) -> str | None:
    """The root command ``token`` selects in the root :func:`build_root_app` last built.

    As dispatch would pick it (exact names, then deprecated aliases, then the
    one loose match); ``None`` when no command, core's or a plugin's, has it.
    """
    return resolve_command(root_app(), token)


def _mount(app: App, sub: App, *, name: str) -> None:
    """Mount ``sub`` as ``name``, replacing any existing command.

    Makes wiring idempotent so ``build_root_app`` can be called more than
    once on the same composition (tests, embedding) without a collision.
    """
    if name in app:
        del app[name]
    app.command(sub, name=name)
    apply_marks(sub, path=(name,))


def _mount_core(root: App, mount: Mount) -> None:
    """Mount the commands on ``Core``'s class at the root, from the declaration."""
    core = mount.declaration.plugin(NAME)
    add_commands(root, mount, NAME)
    for node in core.commands:
        apply_marks(root[node.name], path=(node.name,))


class _LazyPluginCommand(CommandSpec):
    """Cyclopts lazy command backed by a plugin's nullary app factory.

    Cyclopts lists an unresolved :class:`CommandSpec` from its ``help``
    without resolving it, and resolves it only when dispatch selects the
    command. Resolution calls the factory exactly once and applies the same
    parent defaults an eager ``App.command(sub)`` mount would, against the
    root the command was mounted on (not whichever app dispatch passes in,
    which may be the meta app). The private cyclopts internals touched here
    are pinned by ``uv.lock`` and guarded by the internals-presence and
    lazy-vs-eager rendering test in ``tests/repo/test_first_party_composition.py``
    and the internals tests in ``packages/untaped/tests/untaped/runtime/test_bootstrap.py``.
    """

    def __init__(self, plugin: RegisteredPlugin, mount_parent: App) -> None:
        spec = plugin.spec
        super().__init__(
            import_path=f"<plugin {spec.name}>",
            name=spec.name,
            help=spec.help,
            group=_root_panel(spec),  # placed without importing the plugin
        )
        self._plugin = plugin
        self._mount_parent = mount_parent

    def resolve(self, parent_app: App) -> App:
        """Build and cache the plugin app (or its failing stand-in) on first access."""
        resolved = self._resolved
        if resolved is not None:
            return resolved
        built = run_deferred_factory(self._plugin)
        app = _unbuildable_app(built) if isinstance(built, QuarantineRecord) else built
        _apply_parent_defaults_to_app(app, self._mount_parent)
        for flag in chain(app.help_flags, app.version_flags):
            app[flag].show = False
        if app._name_transform is None:
            app.name_transform = self._mount_parent.name_transform
        # Once resolved, cyclopts lists the app by its own group, not the spec's.
        _place(app, self._plugin.spec)
        apply_marks(app, path=(self._plugin.spec.name,))
        self._resolved = app
        return app


def _unbuildable_app(failure: QuarantineRecord) -> App:
    """Stand-in for a plugin whose deferred factory failed.

    Every invocation, ``--help`` included (it declares no help or version
    flags), fails with a ``ConfigError`` (exit 4) attributed to the
    plugin; nothing is unregistered and other plugins are unaffected.
    """
    message = (
        f"plugin {failure.name!r} from {failure.distribution!r} "
        f"could not build its commands: {failure.detail}"
    )
    stub = App(
        name=failure.name,
        help="Unavailable: its commands could not be built.",
        help_flags=(),
        version_flags=(),
    )

    @stub.default
    def _fail(*tokens: Annotated[str, Parameter(allow_leading_hyphen=True)]) -> None:
        note_requested_format(tokens)
        raise ConfigError(message, system=failure.name)

    setattr(_fail, _UNBUILDABLE_ATTR, True)
    return stub


_UNBUILDABLE_ATTR = "__untaped_unbuildable__"


def fail_if_unbuildable(app: App) -> None:
    """Raise the stand-in's ``ConfigError`` when ``app`` is a plugin that could not be built."""
    command = app.default_command
    if command is not None and getattr(command, _UNBUILDABLE_ATTR, False):
        command()


def _root_panel(spec: PluginSpec) -> Group:
    """The root help panel of ``spec``'s command: Plugins, or its stability panel."""
    return PLUGINS_GROUP if spec.stability is None else panel_for(spec.stability)


def _place(app: App, spec: PluginSpec) -> None:
    """Put a plugin's app in its root panel, however it was mounted (its own group is ignored)."""
    app.group = (_root_panel(spec),)
    if spec.stability is not None:
        mark_app(app, spec.stability, source="spec")  # its mark: help line and side table


def _mount_plugin(root: App, plugin: RegisteredPlugin) -> None:
    """Mount one composed plugin's commands, lazily when its factory was deferred."""
    spec = plugin.spec
    if spec.app_factory is None:
        return
    if plugin.app is not None:
        _place(plugin.app, spec)
        _mount(root, plugin.app, name=spec.name)
        return
    if spec.name in root:
        del root[spec.name]
    root._commands[spec.name] = _LazyPluginCommand(plugin, root)


#: Flags that make a command mounted through the 11.x seam a preview
#: (``recipe apply --check``, every ``--dry-run``): the skills check must not
#: write after one. A declared command says so itself (:class:`Dispatched`);
#: this goes when the last 11.x command moves.
_PREVIEW_FLAGS = frozenset({"--dry-run", "--check"})


def _check_skills_after(
    root: App,
    tokens: list[str],
    ran: Sequence[Dispatched],
    skills: Mapping[str, PackagedSkill],
    *,
    failed: bool,
) -> None:
    """Run the per-run installed-skills check after a command.

    Skipped for bare ``untaped``, root flags (``--help``, ``--version``) and
    a command marked ``repairs``, its help and a bad flag included. After a
    failed or previewing command (``--dry-run``, ``Confirm.as_dry_run()``; for
    an 11.x command a ``_PREVIEW_FLAGS`` option before any ``--``) it writes
    nothing: ``warn`` still reports, ``auto`` waits for the next successful
    command. Never lets the check break the command.
    """
    if not tokens or tokens[0].startswith("-"):
        return
    try:
        if _repairs(root, tokens):
            return
        if ran:
            preview = ran[0].preview
        else:
            preview = not _PREVIEW_FLAGS.isdisjoint(_before_separator(tokens))
        check_installed_skills(skills, allow_updates=not (failed or preview))
    except Exception:
        return


def _repairs(root: App, tokens: list[str]) -> bool:
    """Whether ``tokens`` select a command marked ``repairs``, however it ended."""
    app = root
    for token in tokens:
        name = None if token.startswith("-") else resolve_command(app, token)
        if name is None:
            break
        app = app[name]
    return app is not root and repairs_at(app) is True


def _refresh_reference(command_tokens: Sequence[str]) -> None:
    """Keep this install's registered reference in step with the root composition.

    Every start compares, so a write that failed or met a busy lock is
    tried again by the next; one where nothing changed only reads the file.
    It runs here, under the composition the root mounted, never a narrower
    one a command composes. A command waits for a busy lock, since a prune
    elsewhere waits on the same one; root ``--help`` and ``--version`` never
    wait, and print even when the home doesn't resolve (the refresh never
    raises).
    """
    refresh(wait=bool(command_tokens) and not command_tokens[0].startswith("-"))


@contextmanager
def _started(
    tokens: Sequence[str], declaration: Declaration | None
) -> Iterator[tuple[Run, RootLine]]:
    """Start the invocation of ``tokens``: its one run, then its root options applied onto it.

    The token pass reads the line by ``declaration``'s token shapes (with
    none, :func:`run_root` reads it by the composition's, so only when the root
    options apply differs, never what the tokens mean), and the run's rendering
    is fixed from what it names (:func:`_fix_rendering`). The
    run is current for the block; the root attaches the composition to it
    once built (:func:`_attach`). Its home resolves on first read, so a home
    that doesn't resolve fails only what reads it, through the run's sink,
    and a command that never reads it (root ``--help``, ``--version``, a
    group's help) still runs.
    """
    from untaped.runtime.run import entered, start_run  # noqa: PLC0415 - a start runs no command

    with entered(start_run()) as run:
        note_requested_format(tokens)  # a bad root option reports in the format asked for
        line = applied_root_options(run, tokens, _root_options(), declaration)
        _fix_rendering(run, line)
        marker = _STARTED.set((run, line))
        try:
            yield run, line
        finally:
            _STARTED.reset(marker)


def _fix_rendering(run: Run, line: RootLine, root: App | None = None) -> None:
    """Fix ``run``'s rendering from what ``line`` names, unless an 11.x command decides it.

    Before anything else is emitted, and once: the dispatch and the
    command's parse then change neither its format nor its theme
    (:meth:`untaped.sdk._run.Run.render_with` fixes the run's policy too). A first word no
    declaration holds needs the composed ``root`` to tell an unknown command
    (fixed here) from an 11.x seam command, whose parse decides as before
    (:meth:`RootLine.rendering`).
    """
    rendering = line.rendering(root)
    if rendering is not None:
        run.render_with(rendering)


@contextmanager
def _invocation(tokens: Sequence[str], result: CompositionResult, root: App) -> Iterator[RootLine]:
    """The line under the run :func:`run_root` started, or a run started here for ``tokens``.

    The root app driven directly (a test's ``app.meta``) starts its own run
    over the composition it mounted (``root``), reading the line by its
    declaration.
    """
    started = _STARTED.get()
    if started is not None and current_run() is started[0]:
        marker = _STARTED.set(None)  # an invocation nested in the command starts its own
        try:
            yield started[1]
        finally:
            _STARTED.reset(marker)
        return
    with _started(tokens, result.mount.declaration) as (run, line):
        if not line.placed:
            _fix_rendering(run, line, root)
        _attach(run, result)
        yield line


def _install_root_callback(
    app: App,
    root_options: dict[str, _RootOption],
    *,
    result: CompositionResult,
    after_command: Callable[[list[str], Sequence[Dispatched], bool], None] | None = None,
) -> None:
    # The meta app must not intercept --help/--version: that would render the
    # meta callback instead of the inner app's command listing. The inner app
    # handles both flags after the root options are consumed.
    app.meta.help_flags = ()
    app.meta.version_flags = ()
    # Keep ``--`` in the forwarded tokens: the meta parse must not consume it,
    # so the command sees it and root options never match past it.
    app.meta.end_of_options_delimiter = ""

    def _dispatch_root(ran: Sequence[Dispatched], line: RootLine) -> object:
        dispatched = False
        command_tokens = line.tokens
        failed = True
        try:
            with report_errors():
                dispatched = True
                # What dispatch reports before the command parses (the reference
                # refresh's warning, deprecated command and option notices)
                # follows the --format the tokens ask for.
                note_requested_format(command_tokens)
                _refresh_reference(command_tokens)
                outcome = dispatch_line(app, line, root_options, result.mount.declaration)
                # A command that emitted a failure and returned still fails.
                exit_on_counted_failure()
                failed = False
                return outcome
        except SystemExit as exc:
            exc.code = derived_exit_code(exc.code)
            failed = exc.code not in (0, None)
            raise
        finally:
            # Runs on failures too: a stale skill is a likely cause of one.
            if after_command is not None and dispatched:
                after_command(command_tokens, ran, failed)

    def _root_callback(*tokens: str, **_unused: object) -> object:
        # One run covers the invocation, its output the command and the checks
        # after it, so their warnings follow the command's --format: root --help
        # and --version, and any command it dispatches. The pass reads the line
        # as given: the meta parse split its short clusters (`-n5` into `-n -5`).
        with (
            given_tokens(app.meta) as given,
            dispatching() as ran,
            _invocation(tokens if given is None else given, result, app) as line,
        ):
            return _dispatch_root(ran, line)

    signature = _root_callback_signature(root_options)
    _root_callback.__signature__ = signature  # type: ignore[attr-defined]
    _root_callback.__annotations__ = {
        parameter.name: parameter.annotation
        for parameter in signature.parameters.values()
        if parameter.annotation is not inspect.Parameter.empty
    }
    app.meta.default(_root_callback)


def run_root(
    tokens: Iterable[str] | None = None,
    *,
    candidates: Sequence[PluginCandidate] | None = None,
    console: Any | None = None,
    error_console: Any | None = None,
) -> object:
    """Start the invocation's run, compose the root app into it and run it: the ``main()``.

    Before anything else, one pass over the tokens applies the root options,
    reading the line by the kept declaration's token shapes, and starts the
    run, its rendering fixed from what the line names; then the root is
    composed (with no declaration kept, the line is read by its declaration
    now, and a first word no declaration holds is told from an 11.x
    command's by the root app, the rendering fixed then, before anything is
    emitted) and its composition attached to the run, then the tokens are
    dispatched under it. The run's output spans all of it, so composition
    warnings (a quarantined plugin) follow the ``--format`` the tokens ask
    for, like an error found before parsing.
    """
    argv = list(tokens) if tokens is not None else sys.argv[1:]
    found = list(discover_candidates()) if candidates is None else list(candidates)
    with _started(argv, kept_declaration(found)) as (run, line):
        root = build_root_app(candidates=found)
        result = composition()
        if not line.placed:  # no declaration kept, or a word it doesn't hold
            line.read(line.tokens, _root_options(), result.mount.declaration)
            _fix_rendering(run, line, root)
        _attach(run, result)
        return run_cyclopts_app(root.meta, argv, console=console, error_console=error_console)


def main(argv: Sequence[str] | None = None) -> None:
    """Console-script entry point for the unified ``untaped`` shell."""
    forward_signals()
    run_root(argv)


__all__ = [
    "build_root_app",
    "compose_root",
    "fail_if_unbuildable",
    "main",
    "reset",
    "run_root",
]
