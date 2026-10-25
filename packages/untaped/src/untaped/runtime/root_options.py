"""How the root app reads a command line before dispatch.

The root options (``--profile``, ``--verbose``, ``--quiet``, ``--deprecated``),
accepted anywhere before a ``--``. One token pass applies them before the run
starts (:func:`applied_root_options`): it reads the selected command's token
shapes from the declaration, so it takes a root option wherever it sits among
a declared command's own options and never one the command reads as a value.
With no declaration kept, dispatch reads the line the same way by the
composition's. For a command still mounted through the 11.x seam, the pass
takes only the leading ones, and dispatch takes the rest as before (between
command words, around ``--help``, and in the parse retry). Also the rewriting
of a command's loose or deprecated spelling to its registered name, with the
deprecation warnings. The bootstrap composition root uses this one
implementation.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
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
    #: The line was read by a declared command's shape, so every root option in
    #: it is taken and dispatch takes none. ``False`` for a command mounted
    #: through the 11.x seam, or until dispatch reads a line the pass had no
    #: declaration for.
    placed: bool = False
    #: Each root option applied, with what undoes it.
    applied: list[tuple[_RootOption, object]] = field(default_factory=list, init=False)

    def read(
        self,
        tokens: Sequence[str],
        root_options: dict[str, _RootOption],
        declaration: Declaration | None,
    ) -> None:
        """Read ``tokens`` by ``declaration``'s shapes, applying the root options taken."""
        self.tokens, taken, self.placed = _read_line(tokens, root_options, declaration)
        for spec, value in taken:
            self.apply(spec, value)

    def apply(self, spec: _RootOption, value: str) -> None:
        """Apply root option ``spec`` with ``value`` for the rest of the invocation."""
        if spec.name in _CONFLICTING_OPTIONS and any(
            applied.name in _CONFLICTING_OPTIONS and applied.name != spec.name
            for applied, _ in self.applied
        ):
            raise_usage("--verbose and --quiet cannot be combined")
        self.applied.append((spec, spec.handler(value)))

    def _reset(self) -> None:
        """Undo every root option applied, the last applied first."""
        for option, token in reversed(self.applied):
            option.resetter(token)
        self.applied.clear()


@contextmanager
def applied_root_options(
    tokens: Sequence[str],
    root_options: dict[str, _RootOption],
    declaration: Declaration | None = None,
) -> Iterator[RootLine]:
    """Apply the root options of ``tokens`` for the block; the line they leave.

    The pass reads the line by ``declaration``'s token shapes, without
    importing a command (:func:`_read_line`). With no declaration to read it
    by, it takes the leading root options only, and dispatch reads the rest
    by the composition's own (:func:`dispatch_line`): only when they apply
    differs, never what the tokens mean. Each is undone when the block ends.
    """
    line = RootLine(tokens=[])
    try:
        line.read(tokens, root_options, declaration)
        yield line
    finally:
        line._reset()


def _separator_index(tokens: Sequence[str], start: int = 0) -> int:
    """Where the first ``--`` from ``start`` is (``len(tokens)`` when there is none)."""
    return tokens.index("--", start) if "--" in tokens[start:] else len(tokens)


type _Node = CommandDecl | GroupDecl | PluginDecl
type _Taken = list[tuple[_RootOption, str]]


def _read_line(
    tokens: Sequence[str], root_options: dict[str, _RootOption], declaration: Declaration | None
) -> tuple[list[str], _Taken, bool]:
    """What ``tokens`` leave once their root options are taken, those taken, and if all were.

    One walk up to a ``--``: the root options before the command words and
    between them (a ``--`` right after the leading ones only closes them, so
    ``untaped --profile x -- cmd -v`` turns on ``-v``), the words to the
    command ``declaration`` holds for them, then, among that command's own
    options, every root option that is not a value by the command's shape
    (an option's value, or an argument that takes a leading ``-``, such as a
    command line to run). A first word ``declaration`` doesn't hold (an 11.x
    seam command, or any word with no declaration) ends the walk there, with
    only the leading root options taken. A line selecting no command at all
    (``untaped --help -v``) or a group without a command of its own has every
    root option taken.
    """
    walk = _LineWalk(tokens, root_options)
    read, command = walk.command(declaration)
    if not read:
        return list(tokens[walk.index :]), walk.taken, False  # dispatch reads it
    walk.options(command)
    walk.keep(len(tokens) - walk.index)
    return walk.left, walk.taken, True


@dataclass
class _LineWalk:
    """One walk over a line: the tokens it keeps and the root options it takes."""

    tokens: Sequence[str]
    root_options: dict[str, _RootOption]
    index: int = 0
    left: list[str] = field(default_factory=list)
    taken: _Taken = field(default_factory=list)
    #: Where the walk stops: the ``--`` that ends the options.
    end: int = field(init=False)

    def __post_init__(self) -> None:
        self.end = _separator_index(self.tokens)

    def take(self) -> bool:
        """Take the root option at the index, if one is there."""
        found = _root_option_at(self.tokens, self.index, self.root_options)
        if found is not None:
            self.taken.append(found[:2])
            self.index += found[2]
        return found is not None

    def keep(self, count: int) -> None:
        """Keep the next ``count`` tokens for the command."""
        self.left.extend(self.tokens[self.index : self.index + count])
        self.index += count

    def command(self, declaration: Declaration | None) -> tuple[bool, CommandDecl | None]:
        """Walk the command words: whether ``declaration`` holds them, and their command."""
        nodes: Sequence[_Node] = () if declaration is None else _root_nodes(declaration)
        group: GroupDecl | PluginDecl | None = None
        while self.index < self.end and self.take():
            pass  # the leading root options
        if self.index == self.end < len(self.tokens):  # a `--` that only closes them
            self.index += 1
            self.end = _separator_index(self.tokens, self.index)
        while self.index < self.end:
            if self.take():
                continue
            token = self.tokens[self.index]
            node = None if token.startswith("-") else _declared_node(nodes, token)
            if node is None and group is None and not token.startswith("-"):
                return False, None  # a word the declaration doesn't hold
            if node is None:
                break  # an option, or an argument of the group's own command
            self.keep(1)
            if isinstance(node, CommandDecl):
                return True, node
            group, nodes = node, node.commands
        return True, None if group is None else group.runs

    def options(self, command: CommandDecl | None) -> None:
        """Walk ``command``'s own tokens, taking each root option that is none of its values."""
        shapes = _option_shapes(command)
        arguments = [] if command is None else [opt for opt in command.options if opt.positional]
        slot = 0
        while self.index < self.end:
            width = _option_width(self.tokens, self.index, self.end, shapes)
            argument = arguments[slot] if slot < len(arguments) else None
            if not width:
                if (argument is None or not _takes_hyphen(argument)) and self.take():
                    continue
                if argument is not None and not argument.token.startswith("list["):
                    # the next token fills the next argument
                    slot += self.tokens[self.index] not in _HELP_OR_VERSION
                width = 1
            self.keep(width)


def _root_option_at(
    tokens: Sequence[str], index: int, root_options: dict[str, _RootOption]
) -> tuple[_RootOption, str, int] | None:
    """The root option at ``tokens[index]``, its value and how many tokens it spans, if one is.

    A root option's malformed value is a usage error.
    """
    spec = _match_option(tokens[index].partition("=")[0], root_options)
    return None if spec is None else (spec, *_root_value(tokens, index, spec))


def _root_value(tokens: Sequence[str], index: int, spec: _RootOption) -> tuple[str, int]:
    """The value of root option ``spec`` at ``tokens[index]``, and how many tokens it spans."""
    name, separator, inline = tokens[index].partition("=")
    if not spec.takes_value:
        if separator:
            raise_usage(f"{name} takes no value")
        return _FLAG_PRESENT, 1
    if separator:
        if not inline:
            raise_usage(f"{name} expects a value")
        return inline, 1
    if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
        raise_usage(f"{name} expects a value")
    return tokens[index + 1], 2


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
    """The node ``token`` selects, by the rule dispatch picks a command by (:func:`_select`)."""
    named = {node.name: node for node in nodes}
    aliases = {
        alias.name: node.name
        for node in nodes
        if isinstance(node, CommandDecl)
        for alias in node.aliases
    }
    name = _select(token, named, aliases)
    return None if name is None else named[name]


def _option_shapes(command: CommandDecl | None) -> dict[str, OptionDecl]:
    """Each spelling of ``command``'s options (flag, short, negative), with its option."""
    if command is None:
        return {}
    return {
        name: option
        for option in command.options
        if not option.positional
        for name in (option.flag, option.short, option.negative)
        if name
    }


def _option_width(
    tokens: Sequence[str], index: int, end: int, shapes: dict[str, OptionDecl]
) -> int:
    """How many tokens up to ``end`` the command's own option at ``index`` spans (0: none).

    Read as cyclopts reads it: ``--name=value`` (or ``-n=5``) is one token; a
    whole spelling is one for a flag and two for an option taking a value,
    whatever the next token looks like (cyclopts refuses one that looks like an
    option unless it takes a leading ``-``); and an unknown short cluster
    (``-xp``) is its letters left to right, a letter taking a value reading
    the rest of the token (``-n5``) or, ending it, the next token.
    """
    token = tokens[index]
    name, separator, _ = token.partition("=")
    if separator and name in shapes:
        return 1
    if token in shapes:
        takes_next: bool | None = shapes[token].token != "bool"
    elif not token.startswith("--") and len(token) > 2 and is_option_like(token):
        takes_next = _cluster_takes_next(token[1:], shapes)
    else:
        takes_next = None
    if takes_next is None:
        return 0
    return min(2, end - index) if takes_next else 1


def _cluster_takes_next(letters: str, shapes: dict[str, OptionDecl]) -> bool | None:
    """Whether short cluster ``letters`` ends in a letter reading the next token.

    Cyclopts reads the letters left to right: a flag's letter is the flag, an
    unknown one an unknown option, and a letter taking a value reads the rest
    of the token as it. ``None`` when no letter is the command's.
    """
    known = False
    for position, letter in enumerate(letters, 1):
        option = shapes.get(f"-{letter}")
        if option is not None and option.token != "bool":
            return position == len(letters)
        known = known or option is not None
    return False if known else None


def _takes_hyphen(option: OptionDecl) -> bool:
    """Whether ``option``'s value may start with ``-`` (as the mount declares it)."""
    return option.allow_hyphen or option.or_stdin


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
    return _take_root_options(tokens, root_options, line, upto=_separator_index(tokens))


def _take_root_options(
    tokens: Sequence[str], root_options: dict[str, _RootOption], line: RootLine, *, upto: int
) -> list[str]:
    """Apply the root options of ``tokens`` before ``upto`` to ``line``; the tokens left."""
    left: list[str] = []
    index = 0
    while index < len(tokens):
        found = _root_option_at(tokens, index, root_options) if index < upto else None
        if found is None:
            left.append(tokens[index])
            index += 1
            continue
        spec, value, width = found
        line.apply(spec, value)
        index += width
    return left


def _before_separator(tokens: Sequence[str]) -> Sequence[str]:
    """``tokens`` up to a ``--``: what follows it is data for the command, never an option."""
    return tokens[: _separator_index(tokens)]


def dispatch_line(
    app: App, line: RootLine, root_options: dict[str, _RootOption], declaration: Declaration
) -> object:
    """Dispatch the command ``line`` holds; ``declaration`` is the composition's.

    A line the token pass had no declaration for is read now by
    ``declaration``, as the pass reads one (:meth:`RootLine.read`). A placed
    line holds no root option any more, so it parses once. Otherwise (an 11.x
    seam command) dispatch takes the root options the pass left, as before
    the pass: between command words, around
    ``--help``, then optimistically, stripping and applying an unknown root
    option and parsing again. A passthrough command (its ``*args`` absorb
    every token) and a command declaring its own homonymous option parse
    successfully, so they keep the token. Parse errors surface before the
    command body runs, so a retry never repeats side effects.
    """
    with command_selection():
        if not line.placed:
            line.read(line.tokens, root_options, declaration)
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
            value, width = _root_value(remaining, index, spec)
            line.apply(spec, value)
            del remaining[index : index + width]
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

    By :func:`_select`'s rule, over its commands and deprecated aliases.
    """
    aliases = {old: alias.new for old, alias in deprecated_aliases(app).items()}
    return _select(token, app, aliases)


def _select(token: str, names: Iterable[str], aliases: Mapping[str, str]) -> str | None:
    """The one of ``names`` that ``token`` selects, as dispatch picks a command.

    The exact name wins, then an alias (``aliases`` maps it to its name), then
    the one loose (case/``-``/``_``-insensitive) match; ambiguity selects
    nothing.
    """
    if token in names:
        return token
    if token in aliases:
        return aliases[token]
    wanted = _loose_command_key(token)
    matches = [
        name for name in names if not name.startswith("-") and _loose_command_key(name) == wanted
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
        remaining = _take_root_options(remaining, root_options, line, upto=after)
        current = current[name]
        index += 1
    return remaining


def _leading_end(tokens: Sequence[str], root_options: dict[str, _RootOption]) -> int:
    """The index of the first token after the leading root options."""
    index = 0
    while index < len(tokens):
        spec = _match_option(tokens[index].partition("=")[0], root_options)
        if spec is None:
            break
        index += 2 if spec.takes_value and "=" not in tokens[index] else 1
    return min(index, len(tokens))


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


#: Root flags that contradict each other; combining them is a usage error.
_CONFLICTING_OPTIONS = frozenset({"--verbose", "--quiet"})
