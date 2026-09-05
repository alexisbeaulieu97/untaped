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
plumbing with zero built-ins mounted yet. Management commands (``config`` /
``profile`` / ``skills`` / ``doctor`` / ``capabilities``) land in later
waves: the minimal root below mounts whatever validates and lists zero
capabilities when nothing does.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from importlib import metadata
from typing import Annotated, Any, cast

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError, UnknownOptionError
from pydantic import BaseModel

from untaped.capabilities.registry import (
    ApplicationSpec,
    CapabilitySpec,
    CompositionResult,
    ExternalProvider,
    compose,
    discover_external_providers,
)
from untaped.cli import create_app, echo, raise_usage, report_errors, run_cyclopts_app
from untaped.errors import ConfigError
from untaped.profile_resolver import reset_profile_override, set_profile_override
from untaped.quiet import enable as _enable_quiet
from untaped.quiet import reset as _reset_quiet
from untaped.settings import (
    get_profile_settings_model,
    get_settings,
    get_settings_model,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
)
from untaped.verbose import enable as _enable_verbose
from untaped.verbose import reset as _reset_verbose

#: Unified executable name; also the identity reported before dispatch selects
#: a capability (spec §4).
SHELL_NAME = "untaped"

#: Config section owned by the shell itself (spec §1).
SHELL_SECTION = "shell"

#: Distribution owning the unified product version (spec §7.1).
SHELL_DISTRIBUTION = "untaped"

#: Built-in capabilities composed ahead of externals (zero in Wave 1.3;
#: workspace mounts in a later wave).
BUILTIN_CAPABILITIES: tuple[CapabilitySpec, ...] = ()

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

# Placeholder value passed to a flag handler (flags take no value).
_FLAG_PRESENT = ""

_PROFILE_HELP = (
    "Override the active profile for this invocation only "
    "(scoped to the invocation; does not mutate UNTAPED_PROFILE)."
)
_VERBOSE_HELP = "Stream underlying tool output live and enable debug logging."
_QUIET_HELP = "Suppress progress and success/info messages (errors still print)."


@dataclass(frozen=True)
class _RootOption:
    """A position-independent root option backed by a direct handler."""

    name: str
    help: str
    handler: Callable[[str], object]
    resetter: Callable[[object], None]
    aliases: tuple[str, ...] = ()
    takes_value: bool = False


def _apply_profile(value: str) -> object:
    token = set_profile_override(value)
    get_settings.cache_clear()
    return token


def _reset_profile(token: object) -> None:
    reset_profile_override(cast(Token[str | None], token))
    get_settings.cache_clear()


def _reset_verbose_option(token: object) -> None:
    _reset_verbose(cast(Token[bool], token))


def _reset_quiet_option(token: object) -> None:
    _reset_quiet(cast(Token[bool], token))


def _root_options() -> dict[str, _RootOption]:
    return {
        "--profile": _RootOption(
            name="--profile",
            help=_PROFILE_HELP,
            handler=_apply_profile,
            resetter=_reset_profile,
            takes_value=True,
        ),
        "--verbose": _RootOption(
            name="--verbose",
            aliases=("-v",),
            help=_VERBOSE_HELP,
            handler=_enable_verbose,
            resetter=_reset_verbose_option,
        ),
        "--quiet": _RootOption(
            name="--quiet",
            aliases=("-q",),
            help=_QUIET_HELP,
            handler=_enable_quiet,
            resetter=_reset_quiet_option,
        ),
    }


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

    Mounts each validated capability's sub-app under its capability name
    (capability-mount plumbing; zero built-ins mount in Wave 1.3), wires
    ``--version`` to lazy installed-distribution metadata, installs the
    position-independent root options, and registers shell completion. Drive
    ``app.meta`` directly in tests; run via :func:`run_root` in production.
    """
    result = compose_root(builtins=builtins, externals=externals)
    root = create_app(name=SHELL_NAME, help="Unified untaped developer CLI.")
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


def _option_names(option: _RootOption) -> str | tuple[str, ...]:
    if option.aliases:
        return (option.name, *option.aliases)
    return option.name


def _match_option(name: str, root_options: dict[str, _RootOption]) -> _RootOption | None:
    for spec in root_options.values():
        if name == spec.name or name in spec.aliases:
            return spec
    return None


def _consume_option_at(
    tokens: list[str], index: int, spec: _RootOption, name: str
) -> tuple[str, list[str]]:
    if not spec.takes_value:
        if "=" in tokens[index]:
            raise_usage(f"{name} takes no value")
        return _FLAG_PRESENT, tokens[:index] + tokens[index + 1 :]
    return _extract_root_option_value(tokens, index, name)


def _root_callback_signature(root_options: dict[str, _RootOption]) -> inspect.Signature:
    """Build the meta callback signature advertising every root option in help."""
    parameters = [
        inspect.Parameter(
            "tokens",
            inspect.Parameter.VAR_POSITIONAL,
            annotation=Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
        )
    ]
    for index, option in enumerate(root_options.values()):
        annotation: object
        default: object
        if option.takes_value:
            annotation = Annotated[
                str | None,
                Parameter(name=_option_names(option), help=option.help, parse=False, show=True),
            ]
            default = None
        else:
            annotation = Annotated[
                bool,
                Parameter(
                    name=_option_names(option),
                    help=option.help,
                    parse=False,
                    show=True,
                    negative="",
                ),
            ]
            default = False
        parameters.append(
            inspect.Parameter(
                f"_root_option_{index}",
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
    return inspect.Signature(parameters)


def _consume_leading_root_options(
    tokens: list[str],
    root_options: dict[str, _RootOption],
    applied_tokens: list[tuple[_RootOption, object]],
) -> list[str]:
    """Apply and strip root options preceding the command, returning the rest."""
    while tokens:
        name = tokens[0].partition("=")[0]
        spec = _match_option(name, root_options)
        if spec is None:
            break
        value, tokens = _consume_option_at(tokens, 0, spec, name)
        _apply_root_option(spec, value, applied_tokens)
    return tokens


def _dispatch_with_root_options(
    app: App,
    command_tokens: list[str],
    root_options: dict[str, _RootOption],
    applied_tokens: list[tuple[_RootOption, object]],
) -> object:
    """Dispatch optimistically; on unknown root option, strip, apply, retry.

    Passthrough commands parse successfully (their ``*args`` absorb every
    token), so their ``--profile``-looking tokens are never stolen; commands
    declaring their own homonymous option win for the same reason. Parse
    errors surface before the command body runs, so a retry never repeats
    side effects.
    """
    remaining = list(command_tokens)
    applied: set[str] = set()
    while True:
        try:
            return app(
                remaining,
                exit_on_error=False,
                print_error=False,
                result_action="return_value",
            )
        except UnknownOptionError as exc:
            name = _unknown_root_option(exc, root_options)
            if name is None or name in applied:
                echo(f"error: {exc}", err=True)
                raise SystemExit(2) from exc
            applied.add(name)
            spec = root_options[name]
            value, remaining = _strip_trailing_root_option(remaining, spec)
            _apply_root_option(spec, value, applied_tokens)
        except CycloptsError as exc:
            echo(f"error: {exc}", err=True)
            raise SystemExit(2) from exc


def _unknown_root_option(
    exc: UnknownOptionError, root_options: dict[str, _RootOption]
) -> str | None:
    token = getattr(exc, "token", None)
    keyword = getattr(token, "keyword", None) or getattr(token, "value", "")
    if not isinstance(keyword, str):
        return None
    name = keyword.partition("=")[0]
    spec = _match_option(name, root_options)
    return spec.name if spec is not None else None


def _strip_trailing_root_option(tokens: list[str], spec: _RootOption) -> tuple[str, list[str]]:
    """Remove the last occurrence of ``spec`` (by any spelling), returning its value."""
    accepted = (spec.name, *spec.aliases)
    for index in range(len(tokens) - 1, -1, -1):
        head = tokens[index].partition("=")[0]
        if head in accepted:
            return _consume_option_at(tokens, index, spec, head)
    raise_usage(f"{spec.name} expects a value")


def _extract_root_option_value(tokens: list[str], index: int, name: str) -> tuple[str, list[str]]:
    """Pull the value for the root option at ``tokens[index]`` (``--n v`` or ``--n=v``)."""
    _, separator, inline = tokens[index].partition("=")
    if separator:
        if not inline:
            raise_usage(f"{name} expects a value")
        return inline, tokens[:index] + tokens[index + 1 :]
    if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
        raise_usage(f"{name} expects a value")
    return tokens[index + 1], tokens[:index] + tokens[index + 2 :]


def _apply_root_option(
    spec: _RootOption, value: str, applied_tokens: list[tuple[_RootOption, object]]
) -> None:
    applied_tokens.append((spec, spec.handler(value)))


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
