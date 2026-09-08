"""Position-independent root-option machinery for the unified shell.

The ``--profile`` / ``--verbose`` / ``--quiet`` option table and dispatch
helpers live here so the bootstrap composition root has one implementation.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from contextvars import Token
from dataclasses import dataclass
from typing import Annotated, cast

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError, UnknownOptionError

from untaped.cli import echo, raise_usage
from untaped.profile_resolver import reset_profile_override, set_profile_override
from untaped.quiet import enable as _enable_quiet
from untaped.quiet import reset as _reset_quiet
from untaped.settings import get_settings
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
