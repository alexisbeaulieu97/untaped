"""Position-independent root-option machinery for the root app.

The ``--profile`` / ``--verbose`` / ``--quiet`` option table and dispatch
helpers live here so the bootstrap composition root has one implementation.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Annotated, Any

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError, UnknownOptionError

from untaped.cli import note_requested_format, raise_usage
from untaped.deprecated_keys import warn_once
from untaped.errors import UntapedError
from untaped.messages import deprecated_message
from untaped.profile_resolver import profile_scope
from untaped.quiet import enable as _enable_quiet
from untaped.quiet import reset as _reset_quiet
from untaped.settings import load_settings_section
from untaped.stability import (
    Deprecated,
    deprecated_aliases,
    enable_show_deprecated,
    mark_of,
    replacement_text,
    reset_show_deprecated,
)
from untaped.ui import ui_context
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
_DEPRECATED_HELP = "Show deprecated commands too."

#: Tokens that make a run print help or the version instead of running a command.
_HELP_FLAGS = ("--help", "-h")
_HELP_OR_VERSION = (*_HELP_FLAGS, "--version")


@dataclass(frozen=True)
class _RootOption:
    """A position-independent root option backed by a direct handler."""

    name: str
    help: str
    handler: Callable[[str], Any]
    resetter: Callable[[Any], None]
    aliases: tuple[str, ...] = ()
    takes_value: bool = False


def _apply_profile(value: str) -> AbstractContextManager[None]:
    scope = profile_scope(value)
    scope.__enter__()
    return scope


def _reset_profile(scope: AbstractContextManager[None]) -> None:
    scope.__exit__(None, None, None)


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
            resetter=_reset_verbose,
        ),
        "--quiet": _RootOption(
            name="--quiet",
            aliases=("-q",),
            help=_QUIET_HELP,
            handler=_enable_quiet,
            resetter=_reset_quiet,
        ),
        "--deprecated": _RootOption(
            name="--deprecated",
            help=_DEPRECATED_HELP,
            handler=enable_show_deprecated,
            resetter=reset_show_deprecated,
        ),
    }


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
        names = (option.name, *option.aliases)
        annotation: object
        default: object
        if option.takes_value:
            annotation = Annotated[
                str | None, Parameter(name=names, help=option.help, parse=False, show=True)
            ]
            default = None
        else:
            annotation = Annotated[
                bool,
                Parameter(name=names, help=option.help, parse=False, show=True, negative=""),
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


def _before_separator(tokens: Sequence[str]) -> Sequence[str]:
    """``tokens`` up to a ``--``: what follows it is data for the command, never an option."""
    return tokens[: tokens.index("--")] if "--" in tokens else tokens


def _apply_help_root_options(
    tokens: list[str],
    root_options: dict[str, _RootOption],
    applied_tokens: list[tuple[_RootOption, object]],
) -> list[str]:
    """Apply and strip every root option wherever it sits when the run prints help.

    Cyclopts prints help and ignores the tokens after ``--help``, and
    :func:`_consume_path_root_options` leaves an option between the command path
    and ``--help``; either way ``untaped awx --deprecated --help`` and
    ``untaped --help --deprecated`` would drop the option. Tokens after ``--``
    are never touched.
    """
    if not any(token in _HELP_FLAGS for token in _before_separator(tokens)):
        return tokens
    remaining = list(tokens)
    index = 0
    while index < len(_before_separator(remaining)):
        name = remaining[index].partition("=")[0]
        spec = _match_option(name, root_options)
        if spec is None:
            index += 1
            continue
        value, remaining = _consume_option_at(remaining, index, spec, name)
        _apply_root_option(spec, value, applied_tokens)
    return remaining


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
    command_tokens = _apply_help_root_options(command_tokens, root_options, applied_tokens)
    remaining = canonical_command_tokens(
        app, _consume_path_root_options(app, command_tokens, root_options, applied_tokens)
    )
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
            index = (
                None
                if name is None or name in applied
                else _last_root_option_index(remaining, root_options[name])
            )
            if name is None or index is None:
                note_requested_format(remaining)
                raise_usage(str(exc))
            applied.add(name)
            spec = root_options[name]
            value, remaining = _consume_option_at(
                remaining, index, spec, remaining[index].partition("=")[0]
            )
            _apply_root_option(spec, value, applied_tokens)
        except CycloptsError as exc:
            note_requested_format(remaining)
            raise_usage(str(exc))


def canonical_command_tokens(app: App, tokens: Sequence[str]) -> list[str]:
    """Rewrite leading command tokens to their registered spelling.

    Cyclopts resolves a command token loosely (``job_templates`` or
    ``JobTemplates`` for ``job-templates``) but keeps the raw token in the
    command chain, and its help renderer then looks the raw token up exactly
    and crashes with ``KeyError`` (``untaped awx job_templates --help``).
    Substituting the one registered name that the loose match would pick
    keeps the lenient spelling working and gives help the canonical chain.
    Exact names, cyclopts aliases and ambiguous spellings are left for
    cyclopts to handle. Spellings registered with
    :func:`untaped.stability.deprecated_alias` are rewritten too, with a warning:
    command aliases along the chain, then option aliases of the selected
    command (up to a ``--`` separator).
    """
    rewritten = list(tokens)
    current = app
    index = 0
    chain: list[tuple[tuple[str, ...], App]] = []
    consumed = True
    for index, token in enumerate(rewritten):
        if token.startswith("-"):
            consumed = False
            break
        name = _command_name(current, token)
        if name is None:
            consumed = False
            break
        if token not in current and token in deprecated_aliases(current):
            _warn_deprecated(token, name)
        token = rewritten[index] = name
        current = current[token]
        chain.append(((*(chain[-1][0] if chain else ()), token), current))
    if not consumed:
        _rewrite_option_aliases(current, rewritten, index)
    if not any(token in _HELP_OR_VERSION for token in _before_separator(rewritten)):
        _warn_deprecated_command(app, chain)
    return rewritten


def _rewrite_option_aliases(command: App, tokens: list[str], start: int) -> None:
    """Rewrite ``command``'s deprecated option spellings in ``tokens[start:]`` (up to a ``--``)."""
    options = {old: new for old, new in deprecated_aliases(command).items() if old[0] == "-"}
    if not options:
        return
    for position in range(start, len(tokens)):
        name, separator, value = tokens[position].partition("=")
        if name == "--":
            break
        if name in options:
            _warn_deprecated(name, options[name])
            tokens[position] = f"{options[name]}{separator}{value}"


def _warn_deprecated_command(root: App, chain: Sequence[tuple[tuple[str, ...], App]]) -> None:
    """Warn that the run uses a deprecated command, naming the innermost deprecated node."""
    for path, node in reversed(chain):
        mark = mark_of(node)
        if isinstance(mark, Deprecated):
            replacement = replacement_text(mark, root)
            warn_once(deprecated_message(f"`untaped {' '.join(path)}`", replacement))
            return


def resolve_command(app: App, token: str) -> str | None:
    """The command of ``app`` that ``token`` selects (as dispatch would), if any."""
    return _command_name(app, token)


def expand_alias(app: App, tokens: list[str]) -> list[str]:
    """Replace a leading user alias (``shell.aliases``) with the argv it stands for.

    Only a first token that selects no command of ``app`` is looked up, so an
    alias can never shadow a built-in command; the expansion is not expanded
    again. The alias is looked up in the profile a ``--profile`` names
    anywhere before ``--`` (else the active one). Returns ``tokens`` itself
    when nothing expands (including when the ``shell`` settings cannot be
    loaded: the unknown command then fails as usual, and ``doctor`` reports
    the settings).
    """
    if not tokens or tokens[0].startswith("-") or resolve_command(app, tokens[0]) is not None:
        return tokens
    named = _named_profile(tokens[1:])
    try:
        with profile_scope(named) if named else nullcontext():
            aliases = load_settings_section("shell").aliases
    except UntapedError:
        return tokens
    argv = aliases.get(tokens[0])
    if not argv:
        return tokens
    return [*argv, *tokens[1:]]


def _named_profile(tokens: list[str]) -> str | None:
    """The value of the last ``--profile`` before ``--`` in ``tokens``, if well formed."""
    index = _last_root_option_index(tokens, _root_options()["--profile"])
    if index is None:
        return None
    _, separator, inline = tokens[index].partition("=")
    if separator:
        return inline or None
    following = tokens[index + 1 : index + 2]
    return following[0] if following and not following[0].startswith("-") else None


def _command_name(app: App, token: str) -> str | None:
    """The registered subcommand of ``app`` that ``token`` selects, if any.

    Exact names win, then deprecated aliases, then the one loose
    (case/``-``/``_``-insensitive) match; ambiguity selects nothing.
    """
    if token in app:
        return token
    aliases = deprecated_aliases(app)
    if token in aliases:
        return aliases[token]
    wanted = _loose_command_key(token)
    matches = [
        name for name in app if not name.startswith("-") and _loose_command_key(name) == wanted
    ]
    return matches[0] if len(matches) == 1 else None


def _consume_path_root_options(
    app: App,
    tokens: list[str],
    root_options: dict[str, _RootOption],
    applied_tokens: list[tuple[_RootOption, object]],
) -> list[str]:
    """Apply and strip root options sitting between command names.

    ``untaped awx --profile x jobs list`` places a root option after a
    capability (or group) name but before the next command name, where
    cyclopts would read it as an unknown command. Walking the command path
    (resolving lazy capabilities only along the chain dispatch would
    resolve anyway), each run of root options followed by another command
    name is applied and removed. Options after the last command name stay
    in place for the leaf to parse, so a leaf's homonymous option still wins
    and trailing root options go through the retry in
    :func:`_dispatch_with_root_options`.
    """
    remaining = list(tokens)
    current = app
    index = 0
    while index < len(remaining):
        probe = remaining
        pending: list[tuple[_RootOption, str]] = []
        while index < len(probe):
            head = probe[index].partition("=")[0]
            spec = _match_option(head, root_options)
            if spec is None:
                break
            value, probe = _consume_option_at(probe, index, spec, head)
            pending.append((spec, value))
        if index >= len(probe) or probe[index].startswith("-"):
            break
        name = _command_name(current, probe[index])
        if name is None:
            break
        for spec, value in pending:
            _apply_root_option(spec, value, applied_tokens)
        remaining = probe
        current = current[name]
        index += 1
    return remaining


def _warn_deprecated(old: str, new: str) -> None:
    ui_context(strict=False).message("warning", deprecated_message(f"`{old}`", f"`{new}`"))


def _loose_command_key(name: str) -> str:
    """The spelling-insensitive key cyclopts uses to match command tokens."""
    return name.replace("-", "").replace("_", "").lower()


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


def _last_root_option_index(tokens: list[str], spec: _RootOption) -> int | None:
    """Index of the last ``spec`` token (any spelling) before ``--``, if any.

    Tokens after ``--`` are positional data for the command, never root options.
    """
    accepted = (spec.name, *spec.aliases)
    end = tokens.index("--") if "--" in tokens else len(tokens)
    for index in range(end - 1, -1, -1):
        if tokens[index].partition("=")[0] in accepted:
            return index
    return None


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


#: Root flags that contradict each other; combining them is a usage error.
_CONFLICTING_OPTIONS = frozenset({"--verbose", "--quiet"})


def _apply_root_option(
    spec: _RootOption, value: str, applied_tokens: list[tuple[_RootOption, object]]
) -> None:
    if spec.name in _CONFLICTING_OPTIONS and any(
        applied.name in _CONFLICTING_OPTIONS and applied.name != spec.name
        for applied, _ in applied_tokens
    ):
        raise_usage("--verbose and --quiet cannot be combined")
    applied_tokens.append((spec, spec.handler(value)))
