"""How dispatch takes the root options of a command still mounted through the 11.x seam.

The token pass takes only an 11.x command's leading root options
(:mod:`untaped.runtime.root_options`); its dispatch takes the rest as before
the pass: around ``--help``, between command words, and in the parse retry.
The seam goes with this module.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from cyclopts import App
from cyclopts.exceptions import CycloptsError, UnknownOptionError

from untaped.runtime.root_options import (
    _FLAG_PRESENT,
    _HELP_FLAGS,
    RootLine,
    _before_separator,
    _call,
    _Cluster,
    _command_name,
    _LineWalk,
    _match_option,
    _option_width,
    _read_cluster,
    _root_value,
    _RootOption,
    _separator_index,
    canonical_command_tokens,
)
from untaped.sdk._cli import note_requested_format, raise_usage


def dispatch_seam(app: App, line: RootLine, root_options: dict[str, _RootOption]) -> object:
    """Dispatch the 11.x command ``line`` holds, taking the root options the pass left.

    Around ``--help``, between command words, then optimistically, stripping
    and applying an unknown root option and parsing again. A passthrough
    command (its ``*args`` absorb every token) and a command declaring its
    own homonymous option parse successfully, so they keep the token. Parse
    errors surface before the command body runs, so a retry never repeats
    side effects.
    """
    command_tokens = _take_help_root_options(line.tokens, root_options, line)
    remaining = canonical_command_tokens(
        app, _consume_path_root_options(app, command_tokens, root_options, line)
    )
    retried: set[str] = set()
    while True:
        try:
            return _call(app, remaining)
        except CycloptsError as exc:
            if not isinstance(exc, UnknownOptionError) or not _retake(
                exc, remaining, root_options, line, retried
            ):
                note_requested_format(remaining)
                raise_usage(str(exc))


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
    walk = _LineWalk(tokens, root_options)
    walk.options(None)
    walk.keep(len(tokens) - walk.index)
    walk.apply_to(line)
    return walk.left


def _consume_path_root_options(
    app: App, tokens: list[str], root_options: dict[str, _RootOption], line: RootLine
) -> list[str]:
    """Apply and strip root options sitting between an 11.x command's names.

    ``untaped awx --profile x jobs list`` places a root option after a
    plugin (or group) name but before the next command name, where
    cyclopts would read it as an unknown command. Walking the command path
    (resolving lazy plugins only along the chain dispatch would
    resolve anyway), each run of root options followed by another command
    name is applied and removed (a malformed value in any run is a usage
    error). Options after the last command name stay
    in place for the leaf to parse, so a leaf's homonymous option still wins,
    and trailing root options go through the retry in :func:`dispatch_seam`.
    """
    walk = _LineWalk(tokens, root_options)
    current = app
    while walk.index < walk.end:
        ahead = walk.ahead()
        if ahead.problem is not None:
            raise_usage(ahead.problem)
        word = tokens[ahead.index] if ahead.index < ahead.end else "-"
        name = None if word.startswith("-") else _command_name(current, word)
        if name is None:
            break
        walk = ahead
        walk.apply_to(line)
        walk.keep(1)
        current = current[name]
    walk.keep(len(tokens) - walk.index)
    return walk.left


def _retake(
    exc: UnknownOptionError,
    tokens: list[str],
    root_options: dict[str, _RootOption],
    line: RootLine,
    retried: set[str],
) -> bool:
    """Take the root option cyclopts found unknown out of ``tokens``, in place, if it is one.

    The tokens are read by the options cyclopts parsed them by, so an
    option's value is none: the last spelling of it before a ``--``, applied
    to ``line``; or else the first short cluster holding its letter (or help's),
    whose root letters are applied and help letter its own token, as cyclopts
    would read them if they were in scope (``-xv`` is ``-x`` then ``-v``).
    Each root option (and help) is retried once.
    """
    keyword = _unknown_keyword(exc)
    if keyword is None:
        return False
    spec = _match_option(keyword.partition("=")[0], root_options)
    tried = keyword if spec is None else spec.name
    if tried in retried or (spec is None and tried not in _HELP_FLAGS):
        return False
    retried.add(tried)
    spots = list(_options(tokens, _cyclopts_shapes(exc), root_options))
    names = () if spec is None else (spec.name, *spec.aliases)
    spelled = [index for index, _ in spots if tokens[index].partition("=")[0] in names]
    if spec is not None and spelled:
        value, width, problem = _root_value(tokens, spelled[-1], spec)
        if problem is not None:
            raise_usage(problem)
        line.apply(spec, value)
        del tokens[spelled[-1] : spelled[-1] + width]
        return True
    for index, cluster in spots:
        if cluster is not None and (spec in cluster.roots or keyword in cluster.kept):
            tokens[index : index + 1] = cluster.kept
            for root in cluster.roots:
                line.apply(root, _FLAG_PRESENT)
            return True
    return False


def _options(
    tokens: Sequence[str], shapes: Mapping[str, bool], root_options: dict[str, _RootOption]
) -> Iterator[tuple[int, _Cluster | None]]:
    """Where each token before a ``--`` that is no option's value is, with it as a cluster."""
    index, end = 0, _separator_index(tokens)
    while index < end:
        cluster = _read_cluster(tokens[index], shapes, root_options)
        yield index, cluster
        index += _option_width(tokens[index], shapes, cluster) or 1


def _cyclopts_shapes(exc: UnknownOptionError) -> dict[str, bool]:
    """Each spelling of the options cyclopts parsed by, and whether it takes a value."""
    return {
        name: name not in argument.negatives and argument.token_count()[0] > 0
        for argument in getattr(exc, "argument_collection", ())
        if argument.parse
        for name in argument.names
    }


def _unknown_keyword(exc: UnknownOptionError) -> str | None:
    """The option spelling cyclopts found unknown."""
    token = getattr(exc, "token", None)
    keyword = getattr(token, "keyword", None) or getattr(token, "value", "")
    return keyword if isinstance(keyword, str) else None
