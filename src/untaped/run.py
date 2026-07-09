"""Composition root: turn a tool's cyclopts app + ToolSpec into a runnable CLI.

``run_tool(app, spec)`` is a tool's ``main()``. It registers the tool's
settings and the built-in profiles layout, mounts the ``config`` / ``profile``
/ ``skills`` command groups, wires position-independent ``--profile`` /
``--verbose`` root options (usable in any token position, like the retired
hub), overrides the program name to the tool's command, registers shell
completion, and runs under untaped's error-reporting contract.

``build_tool_app`` is the wiring half — it returns the configured app so
callers (and tests) can drive ``app.meta`` directly without running it.

The leading-consume + strip-on-unknown machinery gives ``--profile`` /
``--verbose`` position-independence: a tool's root options are a fixed pair
backed by direct handler callables.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from contextvars import Token
from dataclasses import dataclass
from importlib import metadata
from typing import Annotated, Any, cast

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError, UnknownOptionError

from untaped.cli import echo, raise_usage, report_errors, run_cyclopts_app
from untaped.config import build_config_app
from untaped.errors import ConfigError
from untaped.profile import build_profile_app
from untaped.profile_resolver import reset_profile_override, set_profile_override
from untaped.quiet import enable as _enable_quiet
from untaped.quiet import reset as _reset_quiet
from untaped.settings import get_settings
from untaped.skills_app import build_skills_app
from untaped.tool import ToolSpec, register_tool
from untaped.verbose import enable as _enable_verbose
from untaped.verbose import reset as _reset_verbose

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


def build_tool_app(app: App, spec: ToolSpec) -> App:
    """Wire ``spec`` onto ``app`` and return it ready to run via ``app.meta``."""
    first_wiring = "config" not in app
    register_tool(spec)
    _mount(app, build_config_app(spec), name="config")
    _mount(app, build_profile_app(spec.command), name="profile")
    _mount(app, build_skills_app(spec), name="skills")
    # cyclopts only accepts a name at construction (``App.name`` is a read-only
    # property over the ``_name`` backing field). A tool hands us its own app,
    # so override the backing field to make help/usage read the tool command.
    app._name = (spec.command,)
    distribution = spec.distribution or spec.command

    def resolve_version() -> str:
        try:
            return metadata.version(distribution)
        except metadata.PackageNotFoundError as exc:
            raise ConfigError(
                f"tool {spec.command!r} could not resolve version from "
                f"distribution {distribution!r}"
            ) from exc

    app.version = resolve_version
    if first_wiring:
        # The meta default callback and the completion command can each only be
        # registered once; the mounts above are del-if-present so they re-wire
        # cleanly, but these must be gated to the first wiring.
        _install_root_callback(app, _root_options())
        app.register_install_completion_command()
    return app


def run_tool(
    app: App,
    spec: ToolSpec,
    tokens: Iterable[str] | None = None,
    *,
    console: Any | None = None,
    error_console: Any | None = None,
) -> object:
    """Wire ``spec`` onto ``app`` and run it. Use as a tool's ``main()``."""
    wired = build_tool_app(app, spec)
    return run_cyclopts_app(wired.meta, tokens, console=console, error_console=error_console)


def _mount(app: App, sub: App, *, name: str) -> None:
    """Mount ``sub`` as ``name``, replacing any existing command.

    Makes wiring idempotent so ``build_tool_app`` / ``run_tool`` can be called
    more than once on the same app (tests, embedding) without a collision.
    """
    if name in app:
        del app[name]
    app.command(sub, name=name)


def _install_root_callback(app: App, root_options: dict[str, _RootOption]) -> None:
    # The meta app must not intercept --help/--version: that would render the
    # meta callback instead of the inner app's command listing. The inner app
    # handles both flags after the root options are consumed.
    app.meta.help_flags = ()
    app.meta.version_flags = ()

    def _root_callback(*tokens: str, **_unused: object) -> object:
        # Root-option handlers set invocation-scoped ContextVars (and clear the
        # settings cache). Reset only options this invocation applied so nested
        # in-process callers restore the outer invocation's ContextVars.
        applied_tokens: list[tuple[_RootOption, object]] = []
        try:
            with report_errors():
                command_tokens = _consume_leading_root_options(
                    list(tokens), root_options, applied_tokens
                )
                return _dispatch_with_root_options(
                    app, command_tokens, root_options, applied_tokens
                )
        finally:
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


__all__ = ["build_tool_app", "run_tool"]
