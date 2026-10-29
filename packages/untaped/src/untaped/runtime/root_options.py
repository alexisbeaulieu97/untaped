"""How the root app reads a command line before dispatch.

The root options (``--profile``, ``--verbose``, ``--quiet``, ``--deprecated``),
accepted anywhere before a ``--``. One token pass writes them onto the run
once it started (:func:`applied_root_options`), so they end with it. It reads
the selected command's token shapes from the declaration, so it takes a root
option wherever it sits among a declared command's own options and never one
the command reads as a value. With no declaration kept, the root reads the
line the same way by the composition's once it is composed. Also the
rewriting of a command's loose or deprecated spelling to its registered
name, with the deprecation warnings. The bootstrap composition root uses
this one implementation.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import copy
from dataclasses import dataclass, field, replace
from itertools import chain
from typing import TYPE_CHECKING, Annotated

from cyclopts import App, Parameter
from cyclopts.exceptions import CycloptsError
from cyclopts.utils import is_option_like

from untaped.runtime.declaration import CommandDecl
from untaped.runtime.profile_resolver import check_pick, parse_pick
from untaped.runtime.sink import Diagnostics
from untaped.runtime.verbose import enable as _enable_verbose
from untaped.sdk._cli import (
    follows_user_format,
    note_requested_format,
    parse_error,
    raise_usage,
    requested_format,
    user_default_format,
    user_theme,
)
from untaped.sdk._run import Rendering, Run
from untaped.sdk.errors import ConfigError
from untaped.sdk.marks import (
    Alias,
    Deprecated,
    command_selection,
    deprecated_aliases,
    mark_of,
    marked_options,
    notify_deprecation,
    option_name,
    path_owner,
    replacement_text,
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
    #: Applies the option, with its value, onto the invocation's run; it ends with the run.
    handler: Callable[[Run, str], None]
    aliases: tuple[str, ...] = ()
    takes_value: bool = False


def _apply_profile(run: Run, value: str) -> None:
    """Stack ``value``'s comma list on any earlier ``--profile`` of ``run``, its pick."""
    try:
        names = (*run.profiles, *parse_pick(value, origin="--profile"))
        check_pick(names, origin="--profile")
    except ConfigError as exc:
        Diagnostics().failure(exc)
        raise SystemExit(exc.exit_code) from None
    run.set_profiles(names)


def _quiet(run: Run, _value: str) -> None:
    """Mute ``run``'s commentary: lines the user did not ask for and need not act on.

    That is progress, ``success`` and ``info``. A preview before a prompt, a
    stream the user asked to watch, and output a flag exists to produce are
    not commentary and print (``info(…, unmuted=True)``). Warnings, failures
    and hints always print, as do prompts and stdout.
    """
    run.set_policy(replace(run.policy, quiet=True))


def _deprecated(run: Run, _value: str) -> None:
    """List deprecated things in ``run``'s help too."""
    run.set_policy(replace(run.policy, deprecated=True))


def _root_options() -> dict[str, _RootOption]:
    return {
        "--profile": _RootOption(
            name="--profile",
            help=_PROFILE_HELP,
            handler=_apply_profile,
            takes_value=True,
        ),
        "--verbose": _RootOption(
            name="--verbose",
            aliases=("-v",),
            help=_VERBOSE_HELP,
            handler=_enable_verbose,
        ),
        "--quiet": _RootOption(
            name="--quiet",
            aliases=("-q",),
            help=_QUIET_HELP,
            handler=_quiet,
        ),
        "--deprecated": _RootOption(
            name="--deprecated",
            help=_DEPRECATED_HELP,
            handler=_deprecated,
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

    #: The invocation's run, which every root option the line holds is written onto.
    run: Run
    #: The command's tokens: the line without the root options the pass took
    #: (nor a ``--`` that only closed the leading ones).
    tokens: list[str]
    #: The line was read by a declared command's shape, so every root option in
    #: it is taken and dispatch takes none. ``False`` for a word no declared
    #: command holds, or until dispatch reads a line the pass had no
    #: declaration for.
    placed: bool = False
    #: Each root option applied, in line order.
    applied: list[_RootOption] = field(default_factory=list, init=False)
    #: What a placed line selects, read by its declared shape: the command
    #: (``None`` for no command, or a group without one of its own), the
    #: ``--format`` given among its own options, and whether it asks for help.
    command: CommandDecl | None = field(default=None, init=False)
    format: str | None = field(default=None, init=False)
    helps: bool = field(default=False, init=False)

    def read(
        self,
        tokens: Sequence[str],
        root_options: dict[str, _RootOption],
        declaration: Declaration | None,
    ) -> None:
        """Read ``tokens`` by ``declaration``'s shapes, applying the root options taken."""
        walk, self.placed = _read_line(tokens, root_options, declaration)
        self.tokens = walk.left
        if self.placed:
            self.command, self.format = walk.selected, walk.format
            # cyclopts prints help for a help flag anywhere before a `--`, even an option's value
            self.helps = any(token in _HELP_FLAGS for token in _before_separator(self.tokens))
        walk.apply_to(self)

    def rendering(self, root: App | None = None) -> Rendering | None:
        """How the run renders stderr for the line; ``None`` to leave it to the command's parse.

        For a declared command it is decided as the command's parse decides
        its ``--format`` (:func:`untaped.sdk._cli.apply_default_format`),
        from the declaration alone, so the command's module is not imported:
        the ``--format`` the line gives, else, for a command whose ``Output``
        defaults to ``table``, ``UNTAPED_FORMAT`` and then
        ``untaped.ui.format`` (:func:`untaped.sdk._cli.user_default_format`;
        an invalid ``UNTAPED_FORMAT`` is a usage error unless the command
        ``repairs`` or the line asks for help, which never reads it), else
        none: the command's own default. A line naming no declared command
        (root ``--help`` or ``--version``, bare ``untaped``, a group without
        a command of its own, an unknown command) takes the ``--format`` it
        gives, else ``UNTAPED_FORMAT`` (an invalid one ignored), else
        ``untaped.ui.format``. The theme is the user's under text
        diagnostics. A first word the declaration doesn't hold is an unknown
        command once the composition is mounted (``root`` given); before
        that the line gets ``None``.
        """
        if not self.placed:
            if root is None:
                return None
            return _rendering(self.run, _unnamed_format(self.tokens))
        command = self.command
        if command is None:
            return _rendering(self.run, _unnamed_format(self.tokens))
        chosen: tuple[str, str] | None = None
        if self.format is not None:
            chosen = (self.format, "--format")
        elif _table_default(command):
            chosen = user_default_format(repairs=command.repairs or self.helps)
        return _rendering(self.run, chosen)

    def apply(self, spec: _RootOption, value: str) -> None:
        """Apply root option ``spec`` with ``value``, written onto the line's run."""
        if spec.name in _CONFLICTING_OPTIONS and any(
            applied.name in _CONFLICTING_OPTIONS and applied.name != spec.name
            for applied in self.applied
        ):
            raise_usage("--verbose and --quiet cannot be combined")
        spec.handler(self.run, value)
        self.applied.append(spec)


def applied_root_options(
    run: Run,
    tokens: Sequence[str],
    root_options: dict[str, _RootOption],
    declaration: Declaration | None = None,
) -> RootLine:
    """Write the root options of ``tokens`` onto ``run``, the started one; the line they leave.

    The pass reads the line by ``declaration``'s token shapes, without
    importing a command (:func:`_read_line`). With no declaration to read it
    by, it takes the leading root options only, and the root reads the rest
    by the composition's own once it is composed
    (:func:`untaped.runtime.bootstrap.run_root`), onto the same run: only
    when they apply differs, never what the tokens mean. Each ends with the run.
    """
    line = RootLine(run=run, tokens=[])
    line.read(tokens, root_options, declaration)
    return line


def _separator_index(tokens: Sequence[str], start: int = 0) -> int:
    """Where the first ``--`` from ``start`` is (``len(tokens)`` when there is none)."""
    return tokens.index("--", start) if "--" in tokens[start:] else len(tokens)


type _Node = CommandDecl | GroupDecl | PluginDecl


def _read_line(
    tokens: Sequence[str], root_options: dict[str, _RootOption], declaration: Declaration | None
) -> tuple[_LineWalk, bool]:
    """The walk of ``tokens`` once their root options are taken, and whether it took them all.

    One walk up to a ``--``: the root options before the command words (a
    ``--`` right after them only closes them, so ``untaped --profile x -- cmd
    -v`` turns on ``-v``) and between two of them, the words to the
    command ``declaration`` holds for them, then, among that command's own
    options, every root option that is not a value by the command's shape
    (an option's value, or an argument that takes a leading ``-``, such as a
    command line to run), a root letter of a short cluster included
    (``-xv``). A first word ``declaration`` doesn't hold (an unknown
    command, or any word with no declaration) ends the walk there, with
    only the leading root options taken. A line selecting no command at all
    (``untaped --help -v``) or a group without a command of its own has every
    root option taken.
    """
    walk = _LineWalk(tokens, root_options)
    read, command = walk.command(declaration)
    if read:
        walk.options(command)
    walk.keep(len(tokens) - walk.index)  # data past a `--`, or the rest of a line not read
    return walk, read


@dataclass
class _LineWalk:
    """One walk over a line: the tokens it keeps and the root options it takes."""

    tokens: Sequence[str]
    root_options: dict[str, _RootOption]
    index: int = 0
    left: list[str] = field(default_factory=list)
    #: Each root option taken, in line order: its value, or its usage error
    #: when the value is malformed.
    taken: list[tuple[_RootOption, str, str | None]] = field(default_factory=list)
    #: Where the walk stops: the ``--`` that ends the options.
    end: int = field(init=False)
    #: The command the words select, and the ``--format`` its own options
    #: give (the last one).
    selected: CommandDecl | None = None
    format: str | None = None

    def __post_init__(self) -> None:
        self.end = _separator_index(self.tokens)

    def take(self) -> bool:
        """Take the root option at the index, or a short cluster of root letters only there."""
        token = self.tokens[self.index]
        spec = _match_option(token.partition("=")[0], self.root_options)
        if spec is None:
            cluster = _read_cluster(token, {}, self.root_options)
            if cluster is None or not cluster.roots or cluster.kept:
                return False
            self.split(cluster)
            return True
        value, width, problem = _root_value(self.tokens, self.index, spec)
        self.taken.append((spec, value, problem))
        self.index = min(self.index + width, self.end)
        return True

    def run(self) -> None:
        """Take the run of root options at the index."""
        while self.index < self.end and self.take():
            pass

    def ahead(self) -> _LineWalk:
        """A copy of the walk that took the run of root options at the index.

        Going on from the copy takes them; dropping it leaves a run no command
        word follows, its malformed values with it.
        """
        ahead = copy(self)
        ahead.left, ahead.taken = [*self.left], [*self.taken]
        ahead.run()
        return ahead

    def split(self, cluster: _Cluster) -> None:
        """Take the root letters of ``cluster`` (at the index), and keep the rest of it."""
        self.taken.extend((spec, _FLAG_PRESENT, None) for spec in cluster.roots)
        self.left.extend(cluster.kept)
        self.index += 1
        self.keep(min(cluster.reads_next, self.end - self.index))

    def keep(self, count: int) -> None:
        """Keep the next ``count`` tokens for the command."""
        self.left.extend(self.tokens[self.index : self.index + count])
        self.index += count

    @property
    def problem(self) -> str | None:
        """The usage error of the first root option taken with a malformed value."""
        return next((problem for *_, problem in self.taken if problem is not None), None)

    def apply_to(self, line: RootLine) -> None:
        """Apply the root options taken so far to ``line``, in line order.

        A malformed value is a usage error where it sits, so an earlier
        conflict (``-v -q --profile``) is the one raised.
        """
        for spec, value, problem in self.taken:
            if problem is not None:
                raise_usage(problem)
            line.apply(spec, value)
        self.taken.clear()

    def command(self, declaration: Declaration | None) -> tuple[bool, CommandDecl | None]:
        """Walk the command words: whether ``declaration`` holds them, and their command.

        Root options between words are taken only when another command word
        follows them: otherwise they are the first tokens of the group's own
        command, and its walk reads them.
        """
        nodes: Sequence[_Node] = () if declaration is None else _root_nodes(declaration)
        group: GroupDecl | PluginDecl | None = None
        self.run()  # the leading root options
        if self.index == self.end < len(self.tokens):  # a `--` that only closes them
            self.index += 1
            self.end = _separator_index(self.tokens, self.index)
        while self.index < self.end:
            if group is None:
                if self.take():
                    continue
            elif (ahead := self.ahead()).index > self.index:
                if ahead.index == self.end or _word(nodes, self.tokens[ahead.index]) is None:
                    break  # the first tokens of the group's own command
                vars(self).update(vars(ahead))
                continue
            token = self.tokens[self.index]
            node = _word(nodes, token)
            if node is None and group is None and not token.startswith("-"):
                return False, None  # a word the declaration doesn't hold
            if node is None:
                break  # an option, or an argument of the group's own command
            self.keep(1)
            if isinstance(node, CommandDecl):
                self.selected = node
                return True, node
            group, nodes = node, node.commands
        self.selected = None if group is None else group.runs
        return True, self.selected

    def options(self, command: CommandDecl | None) -> None:
        """Walk ``command``'s own tokens, taking each root option that is none of its values.

        Notes the ``--format`` they give, as the command's parse will read it.
        """
        shapes = _option_shapes(command)
        spellings = _format_spellings(command)
        arguments = [] if command is None else [opt for opt in command.options if opt.positional]
        slot = 0
        while self.index < self.end:
            token = self.tokens[self.index]
            argument = arguments[slot] if slot < len(arguments) else None
            # the token may be the argument's value even when it looks like an option
            value = argument is not None and _takes_hyphen(argument)
            cluster = _read_cluster(token, shapes, self.root_options)
            if cluster is not None and cluster.splits(value):
                self.note_format(cluster, spellings)
                self.split(cluster)
                continue
            width = _option_width(token, shapes, cluster)
            if not width:
                if not value and self.take():
                    continue
                if argument is not None and not argument.token.startswith("list["):
                    # the next token fills the next argument
                    slot += token not in _HELP_OR_VERSION
                width = 1
            else:
                taken = self.tokens[self.index : min(self.index + width, self.end)]
                self.note_format(taken if cluster is None else cluster, spellings)
            self.keep(min(width, self.end - self.index))

    def note_format(self, given: _Cluster | Sequence[str], spellings: tuple[str, ...]) -> None:
        """Note the value ``--format`` gives, when the option at the index is ``--format``.

        ``given`` is the option's tokens (its spelling and its value's), or
        the short cluster it is read as, whose last letter alone may read a
        value: the rest of the token, or the tokens after it.
        """
        if isinstance(given, _Cluster):
            if len(spellings) < 2:  # no short to spell in a cluster
                return
            short, last = spellings[1], given.kept[-1]
            if given.kept[-2:-1] == [short]:
                self.format = last  # `-f` and `=json` apart, as cyclopts reads them
            elif last == short and given.reads_next and self.index + 1 < self.end:
                self.format = self.tokens[self.index + 1]
            elif last.startswith(short) and len(last) > len(short):
                self.format = last[len(short) :]  # `-fjson`
            return
        if not spellings:
            return
        name, separator, inline = given[0].partition("=")
        if name in spellings and separator:
            self.format = inline
        elif given[0] in spellings and len(given) > 1:
            self.format = given[1]


def _root_value(
    tokens: Sequence[str], index: int, spec: _RootOption
) -> tuple[str, int, str | None]:
    """Root option ``spec``'s value at ``tokens[index]``, its tokens, and its usage error if any."""
    name, separator, inline = tokens[index].partition("=")
    if not spec.takes_value:
        return _FLAG_PRESENT, 1, f"{name} takes no value" if separator else None
    if separator:
        return inline, 1, None if inline else f"{name} expects a value"
    if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
        return "", 2, f"{name} expects a value"
    return tokens[index + 1], 2, None


def _root_nodes(declaration: Declaration) -> list[_Node]:
    """What the first command word selects: the root plugins' commands, and each other plugin."""
    nodes: list[_Node] = []
    for plugin in declaration.plugins:
        if plugin.root:
            nodes.extend(plugin.commands)
        else:
            nodes.append(plugin)
    return nodes


def _word(nodes: Sequence[_Node], token: str) -> _Node | None:
    """The node command word ``token`` selects, by the rule dispatch picks a command by.

    That is :func:`_select`'s; an option is no command word.
    """
    if token.startswith("-"):
        return None
    named = {node.name: node for node in nodes}
    aliases = {
        alias.name: node.name
        for node in nodes
        if isinstance(node, CommandDecl)
        for alias in node.aliases
    }
    name = _select(token, named, aliases)
    return None if name is None else named[name]


def _option_shapes(command: CommandDecl | None) -> dict[str, int]:
    """Each spelling of ``command``'s options (flag, short, negative), and its value's tokens."""
    if command is None:
        return {}
    return {
        name: int(option.token != "bool")
        for option in command.options
        if not option.positional
        for name in (option.flag, option.short, option.negative)
        if name
    }


def _table_default(command: CommandDecl) -> bool:
    """Whether ``command``'s ``--format`` offers every shared format and defaults to ``table``.

    Only such a ``--format`` follows the user's default format, as the
    parse reads it (:func:`untaped.sdk._cli.apply_default_format`).
    """
    return any(
        option.flag == "--format" and follows_user_format(option.choices, option.default or "table")
        for option in command.options
    )


def _unnamed_format(tokens: Sequence[str]) -> tuple[str, str] | None:
    """The format a line naming no declared command asks for, and where it came from.

    The last ``--format``/``-f`` in ``tokens``, else the user's default
    format, an invalid ``UNTAPED_FORMAT`` ignored (no command reads it).
    """
    flag = requested_format(tokens)
    return (flag, "--format") if flag is not None else user_default_format(repairs=True)


def _rendering(run: Run, chosen: tuple[str, str] | None) -> Rendering:
    """The rendering of the ``chosen`` format and its source, the user's theme under text."""
    fmt, source = chosen if chosen is not None else (None, None)
    text = replace(run.policy, format=fmt).diagnostics == "text"
    return Rendering(format=fmt, source=source, theme=user_theme() if text else None)


def _format_spellings(command: CommandDecl | None) -> tuple[str, ...]:
    """``command``'s ``--format`` spellings: the flag, then its short; none without one."""
    options = () if command is None else command.options
    option = next((opt for opt in options if opt.flag == "--format"), None)
    if option is None:
        return ()
    return ("--format",) if option.short is None else ("--format", option.short)


def _option_width(token: str, shapes: Mapping[str, int], cluster: _Cluster | None) -> int:
    """How many tokens the command's own option spelled ``token`` spans (0: it is none).

    Read as cyclopts reads it: ``--name=value`` (or ``-n=5``) is one token; a
    whole spelling is one, and its value's tokens for an option taking one,
    whatever the next tokens look like (cyclopts refuses one that looks like an
    option unless it takes a leading ``-``); and a short cluster holding one of
    the command's letters (``cluster``) is one, and the tokens its last letter
    reads next when that letter takes a value.
    """
    name, separator, _ = token.partition("=")
    if separator and name in shapes:
        return 1
    if token in shapes:
        return 1 + shapes[token]
    if cluster is not None and cluster.own:
        return 1 + cluster.reads_next
    return 0


@dataclass(frozen=True)
class _Cluster:
    """A short cluster (``-xvn5``) read letter by letter, as cyclopts reads one."""

    #: What it leaves the command: its own letters (a letter taking a value
    #: with the rest of the token, its value), its unknown ones and a help
    #: letter, each its own token.
    kept: list[str]
    #: The root options its root letters spell, in order.
    roots: list[_RootOption]
    #: One of its letters is the command's.
    own: bool
    #: One of its letters is help's.
    helps: bool
    #: One of its letters is unknown: neither the command's, a root option's nor help's.
    unknown: bool
    #: How many of the next tokens its last letter reads as its value (0: none).
    reads_next: int

    def splits(self, value: bool) -> bool:
        """Whether its root letters and help letter come out of it.

        ``value``: the next argument takes a leading ``-``, and reads the
        cluster as its value unless it is all the command's, root and help
        letters with one of the command's. A cluster with an unknown letter
        is split too otherwise, so cyclopts refuses that letter.
        """
        if not (self.roots or self.helps):
            return False
        return not value or (self.own and not self.unknown)


def _read_cluster(
    token: str, shapes: Mapping[str, int], root_options: dict[str, _RootOption]
) -> _Cluster | None:
    """``token`` read as a short cluster, with the root letters in scope; ``None`` if it is none.

    ``shapes``: each spelling of the command's options, and how many tokens
    its value takes. Cyclopts reads ``-xvn5`` as ``-x``, ``-v``, then ``-n`` reading
    the rest of the token as its value (or, ending the cluster, the next
    token), once no option is spelled ``-xvn5`` (nor ``-xvn`` before an
    ``=``). Here the root shorts (``-v``, ``-q``) and help (``-h``) are
    letters too, as they would be if the command declared them.
    """
    name, separator, _ = token.partition("=")
    if (
        token.startswith("--")
        or len(token) <= 2  # a hyphen and one letter: a whole short
        or not is_option_like(token)
        or token in shapes
        or (separator and name in shapes)
    ):
        return None
    kept: list[str] = []
    roots: list[_RootOption] = []
    own = helps = unknown = False
    letters = token[1:]
    for position, letter in enumerate(letters, 1):
        short = f"-{letter}"
        if shapes.get(short):  # it takes a value: the rest of the token, or the next
            rest = letters[position:]
            # `-n` and `=5` apart: as one token, cyclopts would read `-n=5` as `5`
            kept.extend((short, rest) if rest.startswith("=") else (short + rest,))
            return _Cluster(kept, roots, True, helps, unknown, 0 if rest else shapes[short])
        if short in shapes:
            own = True
        elif (spec := _match_option(short, root_options)) is not None:
            roots.append(spec)
            continue
        elif short in _HELP_FLAGS:
            helps = True
        else:
            unknown = True
        kept.append(short)
    return _Cluster(kept, roots, own, helps, unknown, 0)


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


def _before_separator(tokens: Sequence[str]) -> Sequence[str]:
    """``tokens`` up to a ``--``: what follows it is data for the command, never an option."""
    return tokens[: _separator_index(tokens)]


def dispatch_line(app: App, line: RootLine) -> object:
    """Dispatch the command ``line`` holds, read by the composition's declaration.

    The line holds no root option any more (:meth:`RootLine.read`), so it
    parses once.
    """
    with command_selection():
        return _parsed(app, canonical_command_tokens(app, line.tokens))


def _parsed(app: App, tokens: list[str]) -> object:
    try:
        return _call(app, tokens)
    except CycloptsError as exc:
        note_requested_format(tokens)
        raise_usage(parse_error(exc))


def _call(app: App, tokens: list[str]) -> object:
    return app(tokens, exit_on_error=False, print_error=False, result_action="return_value")


def canonical_command_tokens(app: App, tokens: Sequence[str]) -> list[str]:
    """Rewrite leading command tokens to their registered spelling.

    Cyclopts resolves a command token loosely (``job_templates`` or
    ``JobTemplates`` for ``job-templates``) but keeps the raw token in the
    command chain, and its help renderer then looks the raw token up exactly
    and crashes with ``KeyError`` (``untaped awx job_templates --help``).
    Substituting the one registered name that the loose match would pick
    keeps the lenient spelling working and gives help the canonical chain.
    Exact names, cyclopts aliases and ambiguous spellings are left for
    cyclopts to handle. Command names registered with
    :func:`untaped.sdk.marks.deprecated_alias` along the chain are rewritten
    too, with a warning.
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
            _warn_alias(("untaped", *path), token, aliases[token])
        token = rewritten[index] = name
        current = current[token]
        chain.append(((*path, token), current))
    path = ("untaped", *(chain[-1][0] if chain else ()))
    select_command(app, current, path)
    if not any(token in _HELP_OR_VERSION for token in _before_separator(rewritten)):
        _warn_deprecated_command(app, chain)
        if not consumed:
            _warn_deprecated_options(app, current, path, rewritten[index:])
    return rewritten


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


def _warn_alias(path: tuple[str, ...], old: str, alias: Alias) -> None:
    """Warn about the old command name ``old`` under ``path``, naming both spellings in full.

    Both sides carry the full path (`` `untaped jira me` ``); the id is the
    alias's surface line.
    """
    prefix = " ".join(path)
    notify_deprecation(
        f"cli {prefix} {old} -> {prefix} {alias.new}",
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


def _loose_command_key(name: str) -> str:
    """The spelling-insensitive key cyclopts uses to match command tokens."""
    return name.replace("-", "").replace("_", "").lower()


#: Root flags that contradict each other; combining them is a usage error.
_CONFLICTING_OPTIONS = frozenset({"--verbose", "--quiet"})
