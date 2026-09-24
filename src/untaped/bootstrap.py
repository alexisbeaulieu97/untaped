"""Capability composition root for the unified ``untaped`` shell.

Built-in capabilities and providers discovered through the
``untaped.capabilities`` entry-point group are validated before settings
registration or app mounting. Only providers that survive validation
contribute command trees, settings sections, skills, or doctor checks.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar, Token
from importlib import import_module, metadata
from itertools import chain
from typing import Any

from cyclopts import App
from cyclopts.command_spec import CommandSpec
from cyclopts.core import _apply_parent_defaults_to_app
from pydantic import BaseModel

from untaped._root_options import (
    _consume_leading_root_options,
    _dispatch_with_root_options,
    _root_callback_signature,
    _root_options,
    _RootOption,
)
from untaped.capabilities.registry import (
    ApplicationSpec,
    CapabilitySpec,
    CompositionResult,
    ExternalProvider,
    RegisteredCapability,
    build_deferred_app,
    compose,
    discover_external_providers,
)
from untaped.cli import create_app, echo, report_errors, run_cyclopts_app
from untaped.errors import ConfigError
from untaped.management import (
    build_root_capabilities_app,
    build_root_config_app,
    build_root_doctor_app,
    build_root_profile_app,
    build_root_skills_app,
)
from untaped.management.skills import check_installed_skills, composed_skills
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
from untaped.skills import InstallableSkill
from untaped.verbose import reset as _reset_verbose

#: Unified executable name; also the identity reported before dispatch selects
#: a capability (spec §4).
SHELL_NAME = "untaped"

#: Config section owned by the shell itself (spec §1).
SHELL_SECTION = "shell"

#: Distribution owning the unified product version (spec §7.1).
SHELL_DISTRIBUTION = "untaped"


#: Built-in capabilities composed ahead of external providers, in declaration
#: order. Importing a capability package loads only its ``SPEC``, never its CLI.
BUILTIN_CAPABILITIES: tuple[CapabilitySpec, ...] = tuple(
    import_module(f"untaped.capabilities.{name}").SPEC
    for name in ("workspace", "github", "jira", "awx", "ansible", "recipe")
)

#: Active capability name for the current invocation (spec §4). Set at
#: dispatch time to the selected capability (or the shell name when dispatch
#: has not selected one) and reset to its previous value in a ``finally``
#: block, so nested in-process invocations restore the outer identity.
_active_capability: ContextVar[str | None] = ContextVar("untaped_active_capability", default=None)


def current_capability() -> str | None:
    """Return the active capability name, or ``None`` outside dispatch."""
    return _active_capability.get()


class ShellProfileSettings(BaseModel):
    """Reserved shell-level profile-scoped settings."""


def _shell_app() -> App:
    return create_app(name=SHELL_NAME, help="Unified untaped developer CLI.")


#: The unified shell application (spec §1). A singleton so repeated
#: compositions re-register the identical models idempotently.
SHELL_SPEC = ApplicationSpec(
    name=SHELL_NAME,
    app_factory=_shell_app,
    config_section=SHELL_SECTION,
    profile_model=ShellProfileSettings,
)

_COMPOSED_RESULT: CompositionResult | None = None


def _register_shell_and_capabilities(result: CompositionResult) -> None:
    """Register the shell plus every composed capability's settings sections.

    Runs exactly once per composition, after validation succeeds (spec §5
    Phase D): a provider that fails any row registers nothing.
    """
    specs: list[ApplicationSpec | CapabilitySpec] = [SHELL_SPEC]
    specs.extend(capability.spec for capability in result.capabilities)
    for spec in specs:
        register_profile_settings(spec.config_section, spec.profile_model)
        if spec.state_model is not None:
            register_state_settings(spec.config_section, spec.state_model)


def _warn_quarantined(result: CompositionResult) -> None:
    """Emit one stderr warning per quarantined distribution (spec §5)."""
    seen: set[str] = set()
    for record in result.quarantine:
        if record.distribution in seen:
            continue
        seen.add(record.distribution)
        echo(
            f"warning: capability provider {record.distribution!r} quarantined "
            f"[{record.reason}]: {record.detail}",
            err=True,
        )


def compose_root(
    *,
    builtins: Sequence[CapabilitySpec] = BUILTIN_CAPABILITIES,
    externals: Sequence[ExternalProvider] | None = None,
) -> CompositionResult:
    """Discover, validate, and register one composition.

    Discovery (built-ins plus externals via entry points) runs BEFORE any
    settings registration or resolution; registration happens only after every
    surviving provider validates. Remembers the composition for :func:`reset`.
    """
    global _COMPOSED_RESULT
    candidates = discover_external_providers() if externals is None else externals
    result = compose(SHELL_SPEC, builtins, candidates)
    _register_shell_and_capabilities(result)
    _COMPOSED_RESULT = result
    _warn_quarantined(result)
    return result


def reset() -> None:
    """Clear invocation-scoped state back to the just-composed composition.

    Clears the identity variable, the profile/verbose/quiet overrides, the
    settings caches, and the config registry, then re-registers the
    just-composed shell and capabilities. Exists for test isolation; never
    called implicitly between user invocations (spec §4).
    """
    _active_capability.set(None)
    set_profile_override(None)
    _reset_verbose(None)
    _reset_quiet(None)
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    get_settings_model.cache_clear()
    get_profile_settings_model.cache_clear()
    if _COMPOSED_RESULT is not None:
        _register_shell_and_capabilities(_COMPOSED_RESULT)


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
    builtins: Sequence[CapabilitySpec] = BUILTIN_CAPABILITIES,
    externals: Sequence[ExternalProvider] | None = None,
) -> App:
    """Compose the shell plus capabilities and return the root app.

    Mounts root management commands and each validated capability's sub-app
    under its capability name, wires ``--version`` to installed-distribution
    metadata, installs position-independent root options, and registers shell
    completion. Drive ``app.meta`` directly in tests; run via
    :func:`run_root` in production.
    """
    candidates = list(externals) if externals is not None else list(discover_external_providers())
    result = compose_root(builtins=builtins, externals=candidates)
    root = _shell_app()
    _mount(root, build_root_config_app(shell=SHELL_SPEC, result=result), name="config")
    _mount(root, build_root_profile_app(command=SHELL_NAME), name="profile")
    _mount(root, build_root_skills_app(shell=SHELL_SPEC, result=result), name="skills")
    _mount(root, build_root_doctor_app(shell=SHELL_SPEC, result=result), name="doctor")
    _mount(
        root,
        build_root_capabilities_app(
            result=result,
            candidates=candidates,
            shell_distribution=SHELL_DISTRIBUTION,
        ),
        name="capabilities",
    )
    for capability in result.capabilities:
        _mount_capability(root, capability)
    root.version = _resolve_version
    capability_names = frozenset(capability.spec.name for capability in result.capabilities)
    skills = composed_skills(SHELL_SPEC, result)
    _install_root_callback(
        root,
        _root_options(),
        capability_names,
        after_command=lambda tokens: _check_skills_after(tokens, skills),
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


class _LazyCapabilityCommand(CommandSpec):
    """Cyclopts lazy command backed by a capability's nullary app factory.

    Cyclopts lists an unresolved :class:`CommandSpec` from its ``help``
    without resolving it, and resolves it only when dispatch selects the
    command. Resolution calls the factory exactly once and applies the same
    parent defaults an eager ``App.command(sub)`` mount would, against the
    root the command was mounted on (not whichever app dispatch passes in,
    which may be the meta app). The private cyclopts internals touched here
    are pinned by ``uv.lock`` and guarded by the internals-presence and
    lazy-vs-eager rendering tests in ``tests/unit/test_bootstrap.py``.
    """

    def __init__(self, spec: CapabilitySpec, mount_parent: App) -> None:
        super().__init__(import_path=f"<capability {spec.name}>", name=spec.name, help=spec.help)
        self._capability = spec
        self._mount_parent = mount_parent

    def resolve(self, parent_app: App) -> App:
        """Build, validate, and cache the capability app on first access."""
        resolved = self._resolved
        if resolved is not None:
            return resolved
        spec = self._capability
        app = build_deferred_app(spec)
        _apply_parent_defaults_to_app(app, self._mount_parent)
        for flag in chain(app.help_flags, app.version_flags):
            app[flag].show = False
        if app._name_transform is None:
            app.name_transform = self._mount_parent.name_transform
        self._resolved = app
        return app


def _mount_capability(root: App, capability: RegisteredCapability) -> None:
    """Mount one composed capability, lazily when its factory was deferred."""
    spec = capability.spec
    if capability.app is not None:
        _mount(root, capability.app, name=spec.name)
        return
    if spec.help is None:
        _mount(root, build_deferred_app(spec), name=spec.name)
        return
    if spec.name in root:
        del root[spec.name]
    root._commands[spec.name] = _LazyCapabilityCommand(spec, root)


#: Root commands that manage or diagnose skills themselves: the per-run
#: skills check stays quiet after them.
_SKILLS_CHECK_EXEMPT = frozenset({"skills", "doctor"})


def _check_skills_after(tokens: list[str], skills: Mapping[str, InstallableSkill]) -> None:
    """Run the per-run installed-skills check after a command.

    Skipped for bare ``untaped``, root flags (``--help``, ``--version``) and
    the skills-managing commands. Never lets the check break the command.
    """
    if not tokens or tokens[0].startswith("-") or tokens[0] in _SKILLS_CHECK_EXEMPT:
        return
    try:
        check_installed_skills(skills)
    except Exception:
        return


def _install_root_callback(
    app: App,
    root_options: dict[str, _RootOption],
    capability_names: frozenset[str],
    *,
    after_command: Callable[[list[str]], None] | None = None,
) -> None:
    # The meta app must not intercept --help/--version: that would render the
    # meta callback instead of the inner app's command listing. The inner app
    # handles both flags after the root options are consumed.
    app.meta.help_flags = ()
    app.meta.version_flags = ()
    # Keep ``--`` in the forwarded tokens: the meta parse must not consume it,
    # so the command sees it and root options never match past it.
    app.meta.end_of_options_delimiter = ""

    def _root_callback(*tokens: str, **_unused: object) -> object:
        # Identity is set at dispatch time to the selected capability (or the
        # shell name when dispatch has not selected one) and reset to its
        # previous value in a ``finally`` block, exactly like the root-option
        # reset loop below. Nested in-process callers therefore restore the
        # outer invocation's identity.
        applied_tokens: list[tuple[_RootOption, object]] = []
        identity_token: Token[str | None] | None = None
        command_tokens: list[str] = []
        try:
            with report_errors():
                command_tokens = _consume_leading_root_options(
                    list(tokens), root_options, applied_tokens
                )
                if command_tokens[:1] == ["--"]:
                    command_tokens = command_tokens[1:]  # `untaped [opts] -- cmd …`
                selected = (
                    command_tokens[0]
                    if command_tokens and command_tokens[0] in capability_names
                    else SHELL_NAME
                )
                identity_token = _active_capability.set(selected)
                return _dispatch_with_root_options(
                    app, command_tokens, root_options, applied_tokens
                )
        finally:
            # Runs on failures too: a stale skill is a likely cause of one.
            if after_command is not None and identity_token is not None:
                after_command(command_tokens)
            if identity_token is not None:
                _active_capability.reset(identity_token)
            for option, token in reversed(applied_tokens):
                option.resetter(token)

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
    builtins: Sequence[CapabilitySpec] = BUILTIN_CAPABILITIES,
    externals: Sequence[ExternalProvider] | None = None,
    console: Any | None = None,
    error_console: Any | None = None,
) -> object:
    """Compose the root app and run it. Use as the unified ``main()``."""
    root = build_root_app(builtins=builtins, externals=externals)
    return run_cyclopts_app(root.meta, tokens, console=console, error_console=error_console)


def main(argv: Sequence[str] | None = None) -> None:
    """Console-script entry point for the unified ``untaped`` shell."""
    run_root(argv)


__all__ = [
    "BUILTIN_CAPABILITIES",
    "SHELL_DISTRIBUTION",
    "SHELL_NAME",
    "SHELL_SECTION",
    "SHELL_SPEC",
    "ShellProfileSettings",
    "build_root_app",
    "compose_root",
    "current_capability",
    "main",
    "reset",
    "run_root",
]
