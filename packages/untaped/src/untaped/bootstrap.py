"""Plugin composition root for the unified ``untaped`` shell.

Every plugin is discovered through the ``untaped.plugins``
entry-point group and validated before settings registration or app
mounting. Only providers that survive validation
contribute command trees, settings sections, skills, or doctor checks.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from importlib import metadata
from importlib.resources import files
from itertools import chain
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from cyclopts.command_spec import CommandSpec
from cyclopts.core import _apply_parent_defaults_to_app

from untaped._root_options import (
    _consume_leading_root_options,
    _dispatch_with_root_options,
    _root_callback_signature,
    _root_options,
    _RootOption,
    expand_alias,
    resolve_command,
)
from untaped.cli import (
    apply_default_format,
    echo,
    note_requested_format,
    report_errors,
    run_cyclopts_app,
)
from untaped.diagnostics import diagnostics_scope
from untaped.errors import ConfigError
from untaped.management import (
    build_root_alias_app,
    build_root_auth_app,
    build_root_config_app,
    build_root_doctor_app,
    build_root_plugin_app,
    build_root_profile_app,
    build_root_setup_app,
    build_root_skills_app,
)
from untaped.management.plugins import INSTALL_HINT
from untaped.management.skills import check_installed_skills, composed_skills
from untaped.plugins.registry import (
    ROOT_MANAGEMENT_COMMANDS,
    ApplicationSpec,
    CompositionResult,
    ProviderCandidate,
    QuarantineRecord,
    RegisteredPlugin,
    SkillAsset,
    compose,
    discover_candidates,
    run_deferred_factory,
)
from untaped.profile_resolver import set_profile_override
from untaped.quiet import reset as _reset_quiet
from untaped.settings import (
    get_profile_settings_model,
    get_settings,
    get_settings_model,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
)
from untaped.shell_settings import ShellProfileSettings
from untaped.skills import InstallableSkill
from untaped.stability import ROOT_PARAMETERS_GROUP, apply_marks, mark_app, panel_for
from untaped.verbose import reset as _reset_verbose

#: Unified executable name; also the identity reported before dispatch selects
#: a plugin.
SHELL_NAME = "untaped"

#: Config section owned by the shell itself.
SHELL_SECTION = "shell"

#: Distribution owning the unified product version.
SHELL_DISTRIBUTION = "untaped"


def _shell_app() -> App:
    return App(name=SHELL_NAME, help="Unified untaped developer CLI.")


#: The root application. A singleton so repeated
#: compositions re-register the identical models idempotently.
SHELL_SPEC = ApplicationSpec(
    name=SHELL_NAME,
    app_factory=_shell_app,
    section=SHELL_SECTION,
    settings=ShellProfileSettings,
    skills=(
        SkillAsset(
            name=SHELL_NAME,
            source=Path(str(files("untaped").joinpath("assets", "skills", SHELL_NAME))),
            description=(
                "Installs, sets up and diagnoses the `untaped` CLI itself (profiles, service "
                "URLs and tokens, `setup plan`, `doctor`, output formats and installed agent "
                "skills). Use when the user asks to install or set up untaped, configure or "
                "switch a profile, connect AWX/AAP, GitHub or Jira, fix a failing "
                "`untaped doctor`, or install untaped's skills for an agent."
            ),
        ),
    ),
)

_COMPOSED_RESULT: CompositionResult | None = None


def _register_shell_and_plugins(result: CompositionResult) -> None:
    """Register the shell plus every composed plugin's settings sections.

    Runs exactly once per composition, after validation succeeds: a provider
    that fails any check registers nothing.
    """
    register_profile_settings(SHELL_SPEC.section, SHELL_SPEC.settings)
    if SHELL_SPEC.state is not None:
        register_state_settings(SHELL_SPEC.section, SHELL_SPEC.state)
    for plugin in result.plugins:
        spec = plugin.spec
        if spec.settings is not None:
            register_profile_settings(spec.name, spec.settings, spec.stability)
        if spec.state is not None:
            register_state_settings(spec.name, spec.state)


def _warn_quarantined(result: CompositionResult) -> None:
    """Emit one stderr warning per quarantined plugin."""
    for record in result.quarantine:
        echo(
            f"warning: plugin {record.name!r} from {record.distribution!r} quarantined "
            f"[{record.reason}]: {record.detail}",
            err=True,
        )


def compose_root(
    *,
    candidates: Sequence[ProviderCandidate] | None = None,
) -> CompositionResult:
    """Discover, validate, and register one composition.

    Discovery (entry-point candidates, or ``candidates`` when given) runs BEFORE any
    settings registration or resolution; registration happens only after every
    surviving provider validates. Remembers the composition for :func:`reset`.
    """
    global _COMPOSED_RESULT
    candidates = discover_candidates() if candidates is None else candidates
    result = compose(SHELL_SPEC, candidates)
    _register_shell_and_plugins(result)
    _COMPOSED_RESULT = result
    _warn_quarantined(result)
    return result


def composition() -> CompositionResult:
    """The composition the last :func:`compose_root` (or :func:`build_root_app`) remembered."""
    if _COMPOSED_RESULT is None:
        raise RuntimeError("nothing composed yet; call compose_root() or build_root_app() first")
    return _COMPOSED_RESULT


def reset() -> None:
    """Clear invocation-scoped state back to the just-composed composition.

    Clears the profile/verbose/quiet overrides, the
    settings caches, and the config registry, then re-registers the
    just-composed shell and plugins. Exists for test isolation; never
    called implicitly between user invocations.
    """
    set_profile_override(None)
    _reset_verbose(None)
    _reset_quiet(None)
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    get_settings_model.cache_clear()
    get_profile_settings_model.cache_clear()
    if _COMPOSED_RESULT is not None:
        _register_shell_and_plugins(_COMPOSED_RESULT)


def _clear_for_tests() -> None:
    """Drop the remembered composition entirely (test isolation only)."""
    global _COMPOSED_RESULT
    _COMPOSED_RESULT = None
    reset()


def _resolve_version() -> str:
    try:
        return metadata.version(SHELL_DISTRIBUTION)
    except metadata.PackageNotFoundError as exc:
        raise ConfigError(
            f"shell {SHELL_NAME!r} could not resolve version from "
            f"distribution {SHELL_DISTRIBUTION!r}"
        ) from exc


def build_root_app(
    *,
    candidates: Sequence[ProviderCandidate] | None = None,
) -> App:
    """Compose the shell plus plugins and return the root app.

    Mounts root management commands and each validated plugin's sub-app
    under its plugin name, wires ``--version`` to installed-distribution
    metadata, installs position-independent root options, and registers shell
    completion. Drive ``app.meta`` directly in tests; run via
    :func:`run_root` in production.
    """
    candidates = list(candidates) if candidates is not None else list(discover_candidates())
    result = compose_root(candidates=candidates)
    root = _shell_app()
    root.meta.group_parameters = ROOT_PARAMETERS_GROUP  # keyed, so Parameters sorts last
    if not result.plugins and not result.quarantine:
        root.help = f"{root.help}\n\n{INSTALL_HINT}"
    management = {
        "config": build_root_config_app(shell=SHELL_SPEC, result=result),
        "profile": build_root_profile_app(command=SHELL_NAME),
        "skills": build_root_skills_app(shell=SHELL_SPEC, result=result),
        "doctor": build_root_doctor_app(
            shell=SHELL_SPEC,
            result=result,
            builtin_for=lambda name: resolve_command(root, name),
        ),
        "setup": build_root_setup_app(shell=SHELL_SPEC, result=result),
        "auth": build_root_auth_app(result=result),
        "alias": build_root_alias_app(builtin_for=lambda name: resolve_command(root, name)),
        "plugin": build_root_plugin_app(result=result, candidates=candidates),
    }
    for name in ROOT_MANAGEMENT_COMMANDS:
        _mount(root, management.pop(name), name=name)
    if management:
        raise RuntimeError(f"unreserved management commands: {sorted(management)}")
    for plugin in result.plugins:
        _mount_plugin(root, plugin)
    root.version = _resolve_version
    root.config = (apply_default_format,)
    skills = composed_skills(SHELL_SPEC, result)
    _install_root_callback(
        root,
        _root_options(),
        after_command=lambda tokens, failed: _check_skills_after(tokens, skills, failed=failed),
    )
    root.register_install_completion_command()
    return root


def _mount(app: App, sub: App, *, name: str) -> None:
    """Mount ``sub`` as ``name``, replacing any existing command.

    Makes wiring idempotent so ``build_root_app`` can be called more than
    once on the same composition (tests, embedding) without a collision.
    """
    if name in app:
        del app[name]
    app.command(sub, name=name)
    apply_marks(sub, path=(name,))


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
    and the internals tests in ``packages/untaped/tests/test_bootstrap.py``.
    """

    def __init__(self, plugin: RegisteredPlugin, mount_parent: App) -> None:
        spec = plugin.spec
        super().__init__(
            import_path=f"<plugin {spec.name}>",
            name=spec.name,
            help=spec.help,
            # placed in its panel without importing the plugin
            group=None if spec.stability is None else panel_for(spec.stability),
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
        if self._plugin.spec.stability is not None:
            mark_app(app, self._plugin.spec.stability, source="spec")
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

    return stub


def _mount_plugin(root: App, plugin: RegisteredPlugin) -> None:
    """Mount one composed plugin's commands, lazily when its factory was deferred."""
    spec = plugin.spec
    if spec.app_factory is None:
        return
    if plugin.app is not None:
        if spec.stability is not None:
            mark_app(plugin.app, spec.stability, source="spec")
        _mount(root, plugin.app, name=spec.name)
        return
    if spec.name in root:
        del root[spec.name]
    root._commands[spec.name] = _LazyPluginCommand(plugin, root)


#: Root commands that manage or diagnose skills themselves: the per-run
#: skills check stays quiet after them.
_SKILLS_CHECK_EXEMPT = frozenset({"skills", "doctor"})
#: Flags that make a command a preview (``recipe apply --check``, every
#: ``--dry-run``): the skills check must not write after one.
_PREVIEW_FLAGS = frozenset({"--dry-run", "--check"})


def _check_skills_after(
    tokens: list[str], skills: Mapping[str, InstallableSkill], *, failed: bool
) -> None:
    """Run the per-run installed-skills check after a command.

    Skipped for bare ``untaped``, root flags (``--help``, ``--version``) and
    the skills-managing commands. After a failed or previewing command (a
    ``_PREVIEW_FLAGS`` option before any ``--``) it only reports, never
    updates. Never lets the check break the command.
    """
    if not tokens or tokens[0].startswith("-") or tokens[0] in _SKILLS_CHECK_EXEMPT:
        return
    options = tokens[: tokens.index("--")] if "--" in tokens else tokens
    try:
        preview = not _PREVIEW_FLAGS.isdisjoint(options)
        check_installed_skills(skills, allow_updates=not (failed or preview))
    except Exception:
        return


def _install_root_callback(
    app: App,
    root_options: dict[str, _RootOption],
    *,
    after_command: Callable[[list[str], bool], None] | None = None,
) -> None:
    # The meta app must not intercept --help/--version: that would render the
    # meta callback instead of the inner app's command listing. The inner app
    # handles both flags after the root options are consumed.
    app.meta.help_flags = ()
    app.meta.version_flags = ()
    # Keep ``--`` in the forwarded tokens: the meta parse must not consume it,
    # so the command sees it and root options never match past it.
    app.meta.end_of_options_delimiter = ""

    def _dispatch_root(*tokens: str) -> object:
        applied_tokens: list[tuple[_RootOption, object]] = []
        dispatched = False
        command_tokens: list[str] = []
        failed = True
        try:
            with report_errors():
                command_tokens = _consume_leading_root_options(
                    list(tokens), root_options, applied_tokens
                )
                if command_tokens[:1] == ["--"]:
                    command_tokens = command_tokens[1:]  # `untaped [opts] -- cmd …`
                expanded = expand_alias(app, command_tokens)
                if expanded is not command_tokens:
                    # An alias may start with root options (`--profile prod awx …`).
                    command_tokens = _consume_leading_root_options(
                        expanded, root_options, applied_tokens
                    )
                dispatched = True
                result = _dispatch_with_root_options(
                    app, command_tokens, root_options, applied_tokens
                )
                failed = False
                return result
        except SystemExit as exc:
            failed = exc.code not in (0, None)
            raise
        finally:
            # Runs on failures too: a stale skill is a likely cause of one.
            if after_command is not None and dispatched:
                after_command(command_tokens, failed)
            for option, token in reversed(applied_tokens):
                option.resetter(token)

    def _root_callback(*tokens: str, **_unused: object) -> object:
        # One diagnostics scope covers the command and the checks after it,
        # so their warnings follow the command's --format.
        with diagnostics_scope():
            return _dispatch_root(*tokens)

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
    candidates: Sequence[ProviderCandidate] | None = None,
    console: Any | None = None,
    error_console: Any | None = None,
) -> object:
    """Compose the root app and run it. Use as the unified ``main()``.

    One diagnostics scope spans composition and dispatch, so composition
    warnings (a quarantined provider) follow the ``--format`` the tokens ask
    for, like an error found before parsing.
    """
    argv = list(tokens) if tokens is not None else sys.argv[1:]
    with diagnostics_scope():
        note_requested_format(argv)
        root = build_root_app(candidates=candidates)
        return run_cyclopts_app(root.meta, argv, console=console, error_console=error_console)


def main(argv: Sequence[str] | None = None) -> None:
    """Console-script entry point for the unified ``untaped`` shell."""
    run_root(argv)


__all__ = [
    "SHELL_DISTRIBUTION",
    "SHELL_NAME",
    "SHELL_SECTION",
    "SHELL_SPEC",
    "ShellProfileSettings",
    "build_root_app",
    "compose_root",
    "main",
    "reset",
    "run_root",
]
