"""``cli`` lines: every command, positional and option of a composed root app.

The walk goes through :func:`untaped.stability.children` without resolving
aliases, so a deprecated spelling is seen as an alias. A command's owner line
carries what a caller must pass (required positionals in order, then required
options sorted); every optional positional, option, alias, negative flag and
choice has a line of its own, so adding one adds a line. Flattened dataclass
parameters are containers: their fields are the options. Hidden commands and
parameters are skipped unless deprecated (an option marked ``deprecated(...)``
in its annotation counts) or listed in
:data:`~untaped.surface._lists.WRITTEN_OUTSIDE`. Defaults are the declared
ones, never a value settings would supply at run time.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
from typing import Any

from cyclopts import App

from untaped.stability import children, deprecated_aliases, mark_of, marked_options
from untaped.surface._lines import DeprecatedMark, Line, LineMark
from untaped.surface._lists import WRITTEN_OUTSIDE
from untaped.surface._sdk import SurfaceError, line_mark
from untaped.surface._types import choice_text, default_text

ROOT = "untaped"
_POSITIONAL = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.VAR_POSITIONAL,
)


def cli_lines(root: App, plugin_marks: Mapping[str, LineMark | None]) -> list[Line]:
    """Every ``cli`` line of ``root``; ``plugin_marks`` are the composed plugins' marks."""
    lines = list(_root_options(root))
    lines.extend(_walk(root, root, (ROOT,), None, plugin_marks, top=True))
    return lines


def subtree_lines(root: App, name: str, mark: LineMark | None) -> list[Line]:
    """The ``cli`` lines of the plugin mounted at ``untaped <name>``, the group itself included."""
    for child, sub in children(root, resolve=True):
        if child == name:
            own = line_mark(mark_of(sub), root) or mark
            return [
                *_command(sub, (ROOT, name), own),
                *_walk(root, sub, (ROOT, name), own, {}, top=False),
            ]
    return []


def _root_options(root: App) -> Iterator[Line]:
    """The root's own options, then cyclopts' built-ins: help, version, completion."""
    for argument in root.meta.assemble_argument_collection(parse_docstring=False):
        if not argument.show or argument.field_info.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        yield from _option(f"cli {ROOT}", argument, None)
    flags: set[str] = set()
    for spellings in (root.help_flags, root.version_flags):
        names = _long_first([spellings] if isinstance(spellings, str) else list(spellings))
        flags.update(names)
        if names:
            yield Line(f"cli {ROOT} {names[0]} switch")
            yield from (Line(f"cli {ROOT} {names[0]} alias={alias}") for alias in names[1:])
    for name in sorted(root):
        if name.startswith("-") and name not in flags:
            yield from _command(root[name], (ROOT, name), None)


def _walk(
    root: App,
    app: App,
    path: tuple[str, ...],
    inherited: LineMark | None,
    plugin_marks: Mapping[str, LineMark | None],
    *,
    top: bool,
) -> Iterator[Line]:
    aliases = deprecated_aliases(app)
    for old, alias in sorted(aliases.items()):
        if not old.startswith("-"):
            yield Line(
                f"cli {' '.join((*path, old))} -> {' '.join((*path, alias.new))}",
                DeprecatedMark(alias.removed_in),
            )
    for name, sub in sorted(children(app, resolve=True), key=lambda item: item[0]):
        here = (*path, name)
        own = line_mark(mark_of(sub), root)
        if top and own is None:
            own = plugin_marks.get(name)
        mark = own or inherited
        hidden = sub.show is False
        deprecated = isinstance(mark, DeprecatedMark)
        if hidden and not deprecated and " ".join(here) not in WRITTEN_OUTSIDE:
            continue
        yield from _command(sub, here, mark)
        yield from _walk(root, sub, here, mark, plugin_marks, top=False)


def _command(app: App, path: tuple[str, ...], mark: LineMark | None) -> Iterator[Line]:
    subject = f"cli {' '.join(path)}"
    for old, alias in sorted(deprecated_aliases(app).items()):
        if old.startswith("-"):
            yield Line(f"{subject} {old} -> {alias.new}", DeprecatedMark(alias.removed_in))
    if app.default_command is None:
        yield Line(subject, mark)
        return
    positionals: list[str] = []
    required: list[str] = []
    position = 0
    marked = {argument.field_info.name: option for argument, option in marked_options(app)}
    for argument in _arguments(app, " ".join(path), frozenset(marked)):
        own = line_mark(marked.get(argument.field_info.name)) or mark
        if argument.field_info.kind in _POSITIONAL:
            position += 1
            main = _positional_name(argument)
            name = main + ("..." if _many(argument) else "")
            if argument.required:
                positionals.append(name)
            else:
                default = _default(argument.field_info.default)
                yield Line(f"{subject} [{name}]@{position} default={default}", own)
            yield from _spellings(f"{subject} {main}", argument, own, main=main)
            continue
        if argument.required:
            main = _positive_names(argument)[0]
            required.append(f"{main} {'VALUE...' if _many(argument) else 'VALUE'}")
            yield from _spellings(f"{subject} {main}", argument, own, main=main)
            continue
        yield from _option(subject, argument, own)
    yield Line(" ".join([subject, *positionals, *sorted(required)]), mark)


def _arguments(app: App, where: str, marked: frozenset[str]) -> Iterator[Any]:
    for argument in app.assemble_argument_collection(parse_docstring=False):
        if argument.children:
            continue  # a flattened dataclass: its fields are the options
        kind = argument.field_info.kind
        if kind is inspect.Parameter.VAR_KEYWORD:
            raise SurfaceError(f"{where}: **kwargs parameters have no command-line spelling")
        visible = (
            argument.show
            or argument.field_info.name in marked
            or any(f"{where} {name}" in WRITTEN_OUTSIDE for name in argument.names)
        )
        if not visible or not argument.parse:
            continue
        yield argument


def _positional_name(argument: Any) -> str:
    """``ACTION``; a parameter that is also an option (against the conventions) is ``NAME``
    by its parameter name, and its ``--name`` spelling an alias line."""
    first: str = argument.names[0]
    return first if not first.startswith("-") else str(argument.field_info.name).upper()


def _positive_names(argument: Any) -> list[str]:
    negatives = set(argument.negatives)
    return _long_first([name for name in argument.names if name not in negatives])


def _long_first(names: list[str]) -> list[str]:
    """``names`` with the long spellings first, so ``-o``/``--output`` is under ``--output``."""
    return sorted(names, key=lambda name: not name.startswith("--"))


def _many(argument: Any) -> bool:
    count, consume_all = argument.token_count()
    return bool(consume_all) or count > 1


def _is_switch(argument: Any) -> bool:
    count, consume_all = argument.token_count()
    return count == 0 and not consume_all


def _option(subject: str, argument: Any, mark: LineMark | None) -> Iterator[Line]:
    main = _positive_names(argument)[0]
    yield Line(option_identity(subject, argument), mark)
    yield from _spellings(f"{subject} {main}", argument, mark, main=main)


def option_name(argument: Any) -> str:
    """The spelling an option's line goes under: its first long positive name."""
    return _positive_names(argument)[0]


def option_identity(subject: str, argument: Any) -> str:
    """The identity of an optional option's line: ``cli untaped awx test run --dry-run switch``."""
    main = option_name(argument)
    default = argument.field_info.default
    if _is_switch(argument):
        shape = "switch" if default is False else f"switch default={_default(default)}"
    else:
        shape = f"{'VALUE...' if _many(argument) else 'VALUE'} default={_default(default)}"
    return f"{subject} {main} {shape}"


def _spellings(prefix: str, argument: Any, mark: LineMark | None, *, main: str) -> Iterator[Line]:
    for alias in _positive_names(argument):
        if alias != main:
            yield Line(f"{prefix} alias={alias}", mark)
    for negative in argument.negatives:
        yield Line(f"{prefix} negative={negative}", mark)
    yield from _choices(prefix, argument, mark)


def _choices(prefix: str, argument: Any, mark: LineMark | None) -> Iterator[Line]:
    for choice in argument.get_choices(force=True) or ():
        yield Line(f"{prefix} choice={choice_text(choice)}", mark)


def _default(value: object) -> str:
    if value is inspect.Parameter.empty:
        return "unset"
    return default_text(value, none="unset")
