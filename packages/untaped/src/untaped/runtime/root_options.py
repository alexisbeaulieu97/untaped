"""How the root app reads a command line before dispatch.

The root options (``--profile``, ``--verbose``, ``--quiet``, ``--deprecated``),
accepted anywhere before a ``--``. One token pass applies them before the run
starts (:func:`applied_root_options`): it reads the selected command's token
shapes from the declaration, so it takes a root option wherever it sits among
a declared command's own options and never one the command reads as a value.
For a command still mounted through the 11.x seam, it takes only the leading
ones, and dispatch takes the rest as before (between command words, around
``--help``, and in the parse retry). Also the rewriting of a command's loose or
deprecated spelling to its registered name, with the deprecation warnings.
The bootstrap composition root uses this one implementation.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING, Annotated, Any

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError, UnknownOptionError
from cyclopts.utils import is_option_like

from untaped.runtime.declaration import CommandDecl
from untaped.runtime.output_policy import enable_quiet as _enable_quiet
from untaped.runtime.output_policy import reset_quiet as _reset_quiet
from untaped.runtime.profile_resolver import (
    check_pick,
    parse_pick,
    profile_override,
    profile_scope,
)
from untaped.runtime.sink import Diagnostics
from untaped.runtime.verbose import enable as _enable_verbose
from untaped.runtime.verbose import reset as _reset_verbose
from untaped.sdk._cli import note_requested_format, raise_usage
from untaped.sdk.errors import ConfigError
from untaped.sdk.marks import (
    Alias,
    Deprecated,
    command_selection,
    deprecated_aliases,
    enable_show_deprecated,
    mark_of,
    marked_options,
    notify_deprecation,
    option_name,
    path_owner,
    replacement_text,
    reset_show_deprecated,
    select_command,
)

if TYPE_CHECKING:
    from untaped.runtime.declaration import Declaration, GroupDecl, OptionDecl, PluginDecl

# Placeholder value passed to a flag handler (flags take no value).
_FLAG_PRESENT = ""

_PROFILE_HELP = (
    "Profiles to layer over the base for this invocation, comma-separated or repeated; "
    "stacked after UNTAPED_PROFILE's (`default` reads the base alone)."
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
    """Stack ``value``'s comma list on any earlier ``--profile`` of this run."""
    try:
        names = (*profile_override(), *parse_pick(value, origin="--profile"))
        check_pick(names, origin="--profile")
    except ConfigError as exc:
        Diagnostics().failure(exc)
        raise SystemExit(exc.exit_code) from None
    scope = profile_scope(names)
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


#: Every spelling of a root option. A declared command may use none of them
#: (composition refuses it, rule ``root-option``), so the token pass never has
#: to choose between a root option and the command's own.
ROOT_FLAGS: frozenset[str] = frozenset(
    chain.from_iterable((option.name, *option.aliases) for option in _root_options().values())
)


def _match_option(name: str, root_options: dict[str, _RootOption]) -> _RootOption | None:
    for spec in root_options.values():
        if name == spec.name or name in spec.aliases:
            return spec
    return None


@dataclass
class RootLine:
    """A command line once the token pass took its root options, and what it applied."""

    #: The command's tokens: the line without the root options the pass took
    #: (nor a ``--`` that only closed the leading ones).
    tokens: list[str]
    #: The pass read the line by a declared command's shape, so every root option
    #: in it is taken and dispatch takes none. ``False`` for a command mounted
    #: through the 11.x seam (or with no declaration to read): dispatch takes the
    #: ones after the leading root options as before.
    placed: bool = False
    _applied: list[tuple[_RootOption, object]] = field(default_factory=list)

    def apply(self, spec: _RootOption, value: str) -> None:
        """Apply root option ``spec`` with ``value`` for the rest of the invocation."""
        if spec.name in _CONFLICTING_OPTIONS and any(
            applied.name in _CONFLICTING_OPTIONS and applied.name != spec.name
            for applied, _ in self._applied
        ):
            raise_usage("--verbose and --quiet cannot be combined")
        self._applied.append((spec, spec.handler(value)))

    def _reset(self) -> None:
        """Undo every root option applied, the last applied first."""
        for option, token in reversed(self._applied):
            option.resetter(token)
        self._applied.clear()


@contextmanager
def applied_root_options(
    tokens: Sequence[str],
    root_options: dict[str, _RootOption],
    declaration: Declaration | None = None,
) -> Iterator[RootLine]:
    """Apply the root options of ``tokens`` for the block; the line they leave.

    The pass reads the line before any ``--`` by ``declaration``'s token
    shapes, without importing a command: the root options before the command
    words and between them, then, among the selected command's own options,
    every one that is not a value by the command's shape (an option's value,
    or an argument that takes a leading ``-``, such as a command line to run).
    A ``--`` right after the leading root options only closes them
    (``untaped --profile x -- cmd -v`` turns on ``-v``). A command the
    declaration doesn't hold (one mounted through the 11.x seam) gets only its
    leading root options taken here: dispatch takes the rest
    (:func:`dispatch_line`). Each is undone when the block ends.
    """
    line = RootLine(tokens=[])
    try:
        rest = list(tokens)
        start = _leading_end(rest, root_options)
        if rest[start : start + 1] == ["--"]:
            del rest[start]  # it closed the leading root options, nothing more
        end = _separator_index(rest)
        values = _declared_values(rest, start, end, root_options, declaration)
        line.tokens, taken = _take_root_options(
            rest, root_options, upto=start if values is None else end, values=values or ()
        )
        line.placed = values is not None
        for spec, value in taken:
            line.apply(spec, value)
        yield line
    finally:
        line._reset()


def _separator_index(tokens: Sequence[str]) -> int:
    """Where the first ``--`` is (``len(tokens)`` when there is none)."""
    return tokens.index("--") if "--" in tokens else len(tokens)


def _width(tokens: Sequence[str], index: int, spec: _RootOption) -> int:
    """How many tokens root option ``spec`` at ``index`` spans: itself, and its value apart."""
    return 2 if spec.takes_value and "=" not in tokens[index] else 1


def _leading_end(tokens: Sequence[str], root_options: dict[str, _RootOption]) -> int:
    """The index of the first token after the leading root options."""
    index = 0
    while index < len(tokens):
        spec = _match_option(tokens[index].partition("=")[0], root_options)
        if spec is None:
            break
        index += _width(tokens, index, spec)
    return min(index, len(tokens))


def _take_root_options(
    tokens: Sequence[str],
    root_options: dict[str, _RootOption],
    *,
    upto: int,
    values: Collection[int] = (),
) -> tuple[list[str], list[tuple[_RootOption, str]]]:
    """Split ``tokens`` into what is left and the root options taken from before ``upto``.

    The one scan every pass over a line uses: a token at an index in
    ``values`` is the command's own value, never a root option. A root
    option's malformed value is a usage error.
    """
    left: list[str] = []
    taken: list[tuple[_RootOption, str]] = []
    index = 0
    while index < len(tokens):
        name = tokens[index].partition("=")[0]
        spec = None if index >= upto or index in values else _match_option(name, root_options)
        if spec is None:
            left.append(tokens[index])
            index += 1
            continue
        value, rest = _consume_option_at(list(tokens[index:]), 0, spec, name)
        taken.append((spec, value))
        index = len(tokens) - len(rest)
    return left, taken


type _Node = CommandDecl | GroupDecl | PluginDecl


def _declared_values(
    tokens: Sequence[str],
    start: int,
    end: int,
    root_options: dict[str, _RootOption],
    declaration: Declaration | None,
) -> frozenset[int] | None:
    """The indices of ``tokens`` the selected declared command reads as its own values.

    Walks the command words from ``start``, stepping over root options
    between them, to the command they select, then its own tokens up to
    ``end`` by its declared shape. ``None`` when the words select no
    command the declaration holds (an 11.x seam command), or there is no
    declaration to read them by; a line selecting no command at all
    (``untaped --help -v``) or a group without a command of its own has no
    values.
    """
    index = start
    nodes: Sequence[_Node] = () if declaration is None else _root_nodes(declaration)
    group: GroupDecl | PluginDecl | None = None
    command: CommandDecl | None = None
    while index < end:
        token = tokens[index]
        spec = _match_option(token.partition("=")[0], root_options)
        if spec is not None:
            index += _width(tokens, index, spec)
            continue
        if token.startswith("-"):
            break
        node = _declared_node(nodes, token)
        if node is None:
            if group is None:
                return None  # a word the declaration doesn't hold: dispatch reads it
            break  # an argument of the group's own command
        index += 1
        if isinstance(node, CommandDecl):
            command = node
            break
        group = node
        nodes = node.commands
    if command is None and group is not None:
        command = group.runs
    if command is None:
        return frozenset()
    return _option_values(tokens, index, end, command, root_options)


def _root_nodes(declaration: Declaration) -> list[_Node]:
    """What the first command word selects: the root plugins' commands, and each other plugin."""
    nodes: list[_Node] = []
    for plugin in declaration.plugins:
        if plugin.root:
            nodes.extend(plugin.commands)
        else:
            nodes.append(plugin)
    return nodes


def _declared_node(nodes: Sequence[_Node], token: str) -> _Node | None:
    """The node ``token`` selects, as dispatch picks a command: exact, alias, one loose match."""
    for node in nodes:
        if node.name == token:
            return node
    for node in nodes:
        if isinstance(node, CommandDecl) and any(alias.name == token for alias in node.aliases):
            return node
    wanted = _loose_command_key(token)
    matches = [node for node in nodes if _loose_command_key(node.name) == wanted]
    return matches[0] if len(matches) == 1 else None


def _option_values(
    tokens: Sequence[str],
    index: int,
    end: int,
    command: CommandDecl,
    root_options: dict[str, _RootOption],
) -> frozenset[int]:
    """The indices from ``index`` to ``end`` that ``command`` reads as values, as its parser does.

    An option taking a value reads the next token (one that looks like an
    option only when it takes a leading ``-``); the rest fill the arguments
    in order, a list argument taking every one left. A root option is a
    value only where the argument it would fill takes a leading ``-``.
    """
    flags: dict[str, OptionDecl] = {}
    for option in command.options:
        if not option.positional:
            flags.update((name, option) for name in (option.flag, option.short) if name)
    negatives = {option.negative for option in command.options if option.negative}
    arguments = [option for option in command.options if option.positional]
    values: set[int] = set()
    slot = 0
    while index < end:
        token = tokens[index]
        name = token.partition("=")[0]
        flagged = flags.get(name)
        if flagged is not None or name in negatives:
            index += 1
            if flagged is not None and _takes_next(flagged, token, tokens, index, end):
                values.add(index)
                index += 1
            continue
        argument = arguments[slot] if slot < len(arguments) else None
        spec = _match_option(name, root_options)
        if spec is not None and (argument is None or not _takes_hyphen(argument)):
            index += _width(tokens, index, spec)
            continue
        if token not in _HELP_OR_VERSION:
            values.add(index)
            if argument is not None and not argument.token.startswith("list["):
                slot += 1
        index += 1
    return frozenset(values)


def _takes_hyphen(option: OptionDecl) -> bool:
    """Whether ``option``'s value may start with ``-`` (as the mount declares it)."""
    return option.allow_hyphen or option.or_stdin


def _takes_next(
    option: OptionDecl, token: str, tokens: Sequence[str], index: int, end: int
) -> bool:
    """Whether flag ``option``, given as ``token``, reads ``tokens[index]`` as its value."""
    if option.token == "bool" or "=" in token or index >= end:
        return False
    return _takes_hyphen(option) or not is_option_like(tokens[index])


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


def _take_help_root_options(
    tokens: list[str], root_options: dict[str, _RootOption], line: RootLine
) -> list[str]:
    """Apply and strip every root option before ``--`` when an 11.x command prints help.

    Cyclopts prints help and ignores the tokens after ``--help``, and
    :func:`_consume_path_root_options` leaves an option between the command
    path and ``--help``; either way ``untaped awx --deprecated --help`` would
    drop the option.
    """
    if not any(token in _HELP_FLAGS for token in _before_separator(tokens)):
        return tokens
    left, taken = _take_root_options(tokens, root_options, upto=_separator_index(tokens))
    for spec, value in taken:
        line.apply(spec, value)
    return left


def _before_separator(tokens: Sequence[str]) -> Sequence[str]:
    """``tokens`` up to a ``--``: what follows it is data for the command, never an option."""
    return tokens[: _separator_index(tokens)]


def dispatch_line(app: App, line: RootLine, root_options: dict[str, _RootOption]) -> object:
    """Dispatch the command ``line`` holds.

    A line the token pass placed holds no root option any more, so it
    parses once. Otherwise (an 11.x seam command) dispatch takes the root
    options the pass left, as before the pass: between command words, around
    ``--help``, then optimistically, stripping and applying an unknown root
    option and parsing again. A passthrough command (its ``*args`` absorb
    every token) and a command declaring its own homonymous option parse
    successfully, so they keep the token. Parse errors surface before the
    command body runs, so a retry never repeats side effects.
    """
    with command_selection():
        if line.placed:
            return _parsed(app, canonical_command_tokens(app, line.tokens))
        return _dispatch_seam(app, line, root_options)


def _parsed(app: App, tokens: list[str]) -> object:
    try:
        return _call(app, tokens)
    except CycloptsError as exc:
        note_requested_format(tokens)
        raise_usage(str(exc))


def _call(app: App, tokens: list[str]) -> object:
    return app(tokens, exit_on_error=False, print_error=False, result_action="return_value")


def _dispatch_seam(app: App, line: RootLine, root_options: dict[str, _RootOption]) -> object:
    command_tokens = _take_help_root_options(line.tokens, root_options, line)
    remaining = canonical_command_tokens(
        app, _consume_path_root_options(app, command_tokens, root_options, line)
    )
    retried: set[str] = set()
    while True:
        try:
            return _call(app, remaining)
        except UnknownOptionError as exc:
            name = _unknown_root_option(exc, root_options)
            index = (
                None
                if name is None or name in retried
                else _last_root_option_index(remaining, root_options[name])
            )
            if name is None or index is None:
                note_requested_format(remaining)
                raise_usage(str(exc))
            retried.add(name)
            spec = root_options[name]
            value, remaining = _consume_option_at(
                remaining, index, spec, remaining[index].partition("=")[0]
            )
            line.apply(spec, value)
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
    :func:`untaped.sdk.marks.deprecated_alias` are rewritten too, with a warning:
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
        path = chain[-1][0] if chain else ()
        aliases = deprecated_aliases(current)
        if token not in current and token in aliases:
            _warn_alias(("untaped", *path), token, aliases[token], command=True)
        token = rewritten[index] = name
        current = current[token]
        chain.append(((*path, token), current))
    path = ("untaped", *(chain[-1][0] if chain else ()))
    select_command(app, current, path)
    if not consumed:
        _rewrite_option_aliases(current, path, rewritten, index)
    if not any(token in _HELP_OR_VERSION for token in _before_separator(rewritten)):
        _warn_deprecated_command(app, chain)
        if not consumed:
            _warn_deprecated_options(app, current, path, rewritten[index:])
    return rewritten


def _rewrite_option_aliases(
    command: App, path: tuple[str, ...], tokens: list[str], start: int
) -> None:
    """Rewrite ``command``'s deprecated option spellings in ``tokens[start:]`` (up to a ``--``).

    ``path`` is the command's full path (``("untaped", "logs")``).
    """
    options = {old: alias for old, alias in deprecated_aliases(command).items() if old[0] == "-"}
    if not options:
        return
    for position in range(start, len(tokens)):
        name, separator, value = tokens[position].partition("=")
        if name == "--":
            break
        if name in options:
            _warn_alias(path, name, options[name], command=False)
            tokens[position] = f"{options[name].new}{separator}{value}"


def _warn_deprecated_options(
    root: App, command: App, path: tuple[str, ...], tokens: Sequence[str]
) -> None:
    """Warn for each deprecated option of ``command`` that ``tokens`` use (up to a ``--``).

    Any spelling cyclopts accepts counts (``--dry-run``, ``--no-dry-run``,
    ``--dry-run=false``, a short flag); the warning names the one its surface line goes under,
    with the command's full path, as an alias's warning does.
    """
    marked = marked_options(command)
    if not marked:
        return
    used = {token.partition("=")[0] for token in _before_separator(tokens)}
    for argument, mark in marked:
        if used.isdisjoint(argument.names):
            continue
        from untaped.core.surface._cli import option_identity  # noqa: PLC0415 - only when warning

        notify_deprecation(
            option_identity(f"cli {' '.join(path)}", argument),
            f"`{' '.join(path)} {option_name(argument)}`",
            removed_in=mark.removed_in,
            owner=path_owner(path),
            replacement=replacement_text(mark, root),
        )


def _warn_alias(path: tuple[str, ...], old: str, alias: Alias, *, command: bool) -> None:
    """Warn that ``old`` is a deprecated spelling at ``path``, naming both spellings in full.

    Both sides carry the full path (`` `untaped jira me` ``, `` `untaped logs -F` ``);
    the id is the alias's surface line.
    """
    prefix = " ".join(path)
    new = f"{prefix} {alias.new}" if command else alias.new
    notify_deprecation(
        f"cli {prefix} {old} -> {new}",
        f"`{prefix} {old}`",
        removed_in=alias.removed_in,
        owner=path_owner(path),
        replacement=f"`{prefix} {alias.new}`",
    )


def _warn_deprecated_command(root: App, chain: Sequence[tuple[tuple[str, ...], App]]) -> None:
    """Warn that the run uses a deprecated command, naming the innermost deprecated node."""
    for path, node in reversed(chain):
        mark = mark_of(node)
        if isinstance(mark, Deprecated):
            full = ("untaped", *path)
            notify_deprecation(
                f"cli {' '.join(full)}",
                f"`{' '.join(full)}`",
                removed_in=mark.removed_in,
                owner=path_owner(full),
                replacement=replacement_text(mark, root),
            )
            return


def resolve_command(app: App, token: str) -> str | None:
    """The command of ``app`` that ``token`` selects (as dispatch would), if any."""
    return _command_name(app, token)


def _command_name(app: App, token: str) -> str | None:
    """The registered subcommand of ``app`` that ``token`` selects, if any.

    Exact names win, then deprecated aliases, then the one loose
    (case/``-``/``_``-insensitive) match; ambiguity selects nothing.
    """
    if token in app:
        return token
    aliases = deprecated_aliases(app)
    if token in aliases:
        return aliases[token].new
    wanted = _loose_command_key(token)
    matches = [
        name for name in app if not name.startswith("-") and _loose_command_key(name) == wanted
    ]
    return matches[0] if len(matches) == 1 else None


def _consume_path_root_options(
    app: App, tokens: list[str], root_options: dict[str, _RootOption], line: RootLine
) -> list[str]:
    """Apply and strip root options sitting between an 11.x command's names.

    ``untaped awx --profile x jobs list`` places a root option after a
    plugin (or group) name but before the next command name, where
    cyclopts would read it as an unknown command. Walking the command path
    (resolving lazy plugins only along the chain dispatch would
    resolve anyway), each run of root options followed by another command
    name is applied and removed. Options after the last command name stay
    in place for the leaf to parse, so a leaf's homonymous option still wins,
    and trailing root options go through the retry in :func:`dispatch_line`.
    """
    remaining = list(tokens)
    current = app
    index = 0
    while index < len(remaining):
        after = index + _leading_end(remaining[index:], root_options)
        if after >= len(remaining) or remaining[after].startswith("-"):
            break
        name = _command_name(current, remaining[after])
        if name is None:
            break
        remaining, taken = _take_root_options(remaining, root_options, upto=after)
        for spec, value in taken:
            line.apply(spec, value)
        current = current[name]
        index += 1
    return remaining


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
