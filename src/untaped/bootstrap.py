"""Capability composition root for the unified ``untaped`` shell (spec §§1-2,4).

This module REPLACES the ``untaped.run`` / ``untaped.tool`` composition path:
it deliberately imports neither module. Discovery of built-in capabilities
plus externals via the ``untaped.capabilities`` entry-point group runs BEFORE
any settings registration or resolution; only providers that survive
validation register settings sections, mount apps, or contribute skills and
doctor checks.

Wave 1.3 scope: the root owns position-independent ``--profile`` /
``--verbose`` / ``--quiet`` options (token-reset exactly like the retired
per-tool root), lazy ``--version`` from the ``untaped`` distribution,
shell-completion wiring, invocation-scoped identity, and capability-mount
plumbing with zero built-ins mounted yet. Wave 1.4 mounts the five root
management commands (``config`` / ``profile`` / ``skills`` / ``doctor`` /
``capabilities``, see :mod:`untaped.management`); Wave 1.5 mounts the
workspace built-in capability subtree (``untaped workspace ...``).
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Sequence
from contextvars import ContextVar, Token
from importlib import metadata
from typing import Any

from cyclopts import App
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
from untaped.verbose import reset as _reset_verbose

#: Unified executable name; also the identity reported before dispatch selects
#: a capability (spec §4).
SHELL_NAME = "untaped"

#: Config section owned by the shell itself (spec §1).
SHELL_SECTION = "shell"

#: Distribution owning the unified product version (spec §7.1).
SHELL_DISTRIBUTION = "untaped"

def _workspace_builtins() -> tuple[CapabilitySpec, ...]:
    """Return the workspace built-in without importing its CLI tree at module load."""
    from untaped.capabilities.workspace import SPEC  # noqa: PLC0415

    return (SPEC,)


#: Built-in capabilities composed ahead of externals (Wave 1.5 mounts the
#: workspace capability; further capabilities append in declaration order).
BUILTIN_CAPABILITIES: tuple[CapabilitySpec, ...] = _workspace_builtins()

#: Active capability name for the current invocation (spec §4). Set at
#: dispatch time to the selected capability (or the shell name when dispatch
#: has not selected one) and reset to its previous value in a ``finally``
#: block, so nested in-process invocations restore the outer identity.
_active_capability: ContextVar[str | None] = ContextVar("untaped_active_capability", default=None)


def current_capability() -> str | None:
    """Return the active capability name, or ``None`` outside dispatch."""
    return _active_capability.get()


class ShellProfileSettings(BaseModel):
    """Shell-level profile-scoped settings (reserved; no fields in Wave 1.3)."""


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

_COMPOSED_SHELL: ApplicationSpec | None = None
_COMPOSED_RESULT: CompositionResult | None = None


def _register_shell_and_capabilities(shell: ApplicationSpec, result: CompositionResult) -> None:
    """Register the shell plus every composed capability's settings sections.

    Runs exactly once per composition, after validation succeeds (spec §5
    Phase D): a provider that fails any row registers nothing.
    """
    register_profile_settings(shell.config_section, shell.profile_model)
    if shell.state_model is not None:
        register_state_settings(shell.config_section, shell.state_model)
    for capability in result.capabilities:
        register_profile_settings(capability.spec.config_section, capability.spec.profile_model)
        if capability.spec.state_model is not None:
            register_state_settings(capability.spec.config_section, capability.spec.state_model)


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
    global _COMPOSED_SHELL, _COMPOSED_RESULT
    shell = SHELL_SPEC
    candidates = discover_external_providers() if externals is None else externals
    result = compose(shell, builtins, candidates)
    _register_shell_and_capabilities(shell, result)
    _COMPOSED_SHELL = shell
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
    shell = _COMPOSED_SHELL
    result = _COMPOSED_RESULT
    if shell is not None and result is not None:
        _register_shell_and_capabilities(shell, result)


def _clear_for_tests() -> None:
    """Drop the remembered composition entirely (test isolation only)."""
    global _COMPOSED_SHELL, _COMPOSED_RESULT
    _COMPOSED_SHELL = None
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

    Mounts the five root management commands plus each validated
    capability's sub-app under its capability name (Wave 1.5 default mounts
    the workspace built-in), wires ``--version`` to lazy installed-distribution
    metadata,
    installs the position-independent root options, and registers shell
    completion. Drive ``app.meta`` directly in tests; run via :func:`run_root`
    in production.
    """
    candidates = list(externals) if externals is not None else list(discover_external_providers())
    result = compose_root(builtins=builtins, externals=candidates)
    root = create_app(name=SHELL_NAME, help="Unified untaped developer CLI.")
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
        _mount(root, capability.spec.app_factory(), name=capability.spec.name)
    root.version = _resolve_version
    capability_names = frozenset(capability.spec.name for capability in result.capabilities)
    _install_root_callback(root, _root_options(), capability_names)
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


def _install_root_callback(
    app: App,
    root_options: dict[str, _RootOption],
    capability_names: frozenset[str],
) -> None:
    # The meta app must not intercept --help/--version: that would render the
    # meta callback instead of the inner app's command listing. The inner app
    # handles both flags after the root options are consumed.
    app.meta.help_flags = ()
    app.meta.version_flags = ()

    def _root_callback(*tokens: str, **_unused: object) -> object:
        # Identity is set at dispatch time to the selected capability (or the
        # shell name when dispatch has not selected one) and reset to its
        # previous value in a ``finally`` block, exactly like the root-option
        # reset loop below. Nested in-process callers therefore restore the
        # outer invocation's identity.
        applied_tokens: list[tuple[_RootOption, object]] = []
        identity_token: Token[str | None] | None = None
        try:
            with report_errors():
                command_tokens = _consume_leading_root_options(
                    list(tokens), root_options, applied_tokens
                )
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
