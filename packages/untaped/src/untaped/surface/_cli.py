"""``cli`` lines: every command, positional and option of a composed root app.

The walk goes through :func:`untaped.stability.children` without resolving
aliases, so a deprecated spelling is seen as an alias. A command's owner line
carries what a caller must pass (required positionals in order, then required
options sorted); every optional positional, option, alias, negative flag and
choice has a line of its own, so adding one adds a line. Flattened dataclass
parameters are containers: their fields are the options. Hidden commands and
parameters are skipped unless deprecated or listed in
:data:`~untaped.surface._lists.WRITTEN_OUTSIDE`. Defaults are the declared
ones, never a value settings would supply at run time.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
from typing import Any

from cyclopts import App

from untaped.stability import children, deprecated_aliases, mark_of
from untaped.surface._lines import Line, MarkName
from untaped.surface._lists import WRITTEN_OUTSIDE
from untaped.surface._sdk import SurfaceError, mark_name
from untaped.surface._types import choice_text, default_text

ROOT = "untaped"
_POSITIONAL = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.VAR_POSITIONAL,
)


def cli_lines(root: App, plugin_marks: Mapping[str, MarkName | None]) -> list[Line]:
    """Every ``cli`` line of ``root``; ``plugin_marks`` are the composed plugins' marks."""
    lines = list(_root_options(root))
    lines.extend(_walk(root, (ROOT,), None, plugin_marks, top=True))
    return lines


def subtree_lines(root: App, name: str, mark: MarkName | None) -> list[Line]:
    """The ``cli`` lines of the plugin mounted at ``untaped <name>``, the group itself included."""
    for child, sub in children(root, resolve=True):
        if child == name:
            own = mark_name(mark_of(sub)) or mark
            return [
                *_command(sub, (ROOT, name), own),
                *_walk(sub, (ROOT, name), own, {}, top=False),
            ]
    return []


def _root_options(root: App) -> Iterator[Line]:
    for argument in root.meta.assemble_argument_collection(parse_docstring=False):
        if not argument.show or argument.field_info.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        yield from _option(f"cli {ROOT}", argument, None)


def _walk(
    app: App,
    path: tuple[str, ...],
    inherited: MarkName | None,
    plugin_marks: Mapping[str, MarkName | None],
    *,
    top: bool,
) -> Iterator[Line]:
    aliases = deprecated_aliases(app)
    for old, new in sorted(aliases.items()):
        if not old.startswith("-"):
            yield Line(f"cli {' '.join((*path, old))} -> {' '.join((*path, new))}", "deprecated")
    for name, sub in sorted(children(app, resolve=True), key=lambda item: item[0]):
        here = (*path, name)
        own = mark_name(mark_of(sub))
        if top and own is None:
            own = plugin_marks.get(name)
        mark = own or inherited
        hidden = sub.show is False
        if hidden and mark != "deprecated" and " ".join(here) not in WRITTEN_OUTSIDE:
            continue
        yield from _command(sub, here, mark)
        yield from _walk(sub, here, mark, plugin_marks, top=False)


def _command(app: App, path: tuple[str, ...], mark: MarkName | None) -> Iterator[Line]:
    subject = f"cli {' '.join(path)}"
    for old, new in sorted(deprecated_aliases(app).items()):
        if old.startswith("-"):
            yield Line(f"{subject} {old} -> {new}", "deprecated")
    if app.default_command is None:
        yield Line(subject, mark)
        return
    positionals: list[str] = []
    required: list[str] = []
    position = 0
    for argument in _arguments(app, " ".join(path)):
        if argument.field_info.kind in _POSITIONAL:
            position += 1
            main = _positional_name(argument)
            name = main + ("..." if _many(argument) else "")
            if argument.required:
                positionals.append(name)
            else:
                default = _default(argument.field_info.default)
                yield Line(f"{subject} [{name}]@{position} default={default}", mark)
            yield from _spellings(f"{subject} {main}", argument, mark, main=main)
            continue
        if argument.required:
            main = _positive_names(argument)[0]
            required.append(f"{main} {'VALUE...' if _many(argument) else 'VALUE'}")
            yield from _spellings(f"{subject} {main}", argument, mark, main=main)
            continue
        yield from _option(subject, argument, mark)
    yield Line(" ".join([subject, *positionals, *sorted(required)]), mark)


def _arguments(app: App, where: str) -> Iterator[Any]:
    for argument in app.assemble_argument_collection(parse_docstring=False):
        if argument.children:
            continue  # a flattened dataclass: its fields are the options
        kind = argument.field_info.kind
        if kind is inspect.Parameter.VAR_KEYWORD:
            raise SurfaceError(f"{where}: **kwargs parameters have no command-line spelling")
        visible = argument.show or any(
            f"{where} {name}" in WRITTEN_OUTSIDE for name in argument.names
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
    return [name for name in argument.names if name not in negatives]


def _many(argument: Any) -> bool:
    count, consume_all = argument.token_count()
    return bool(consume_all) or count > 1


def _is_switch(argument: Any) -> bool:
    count, consume_all = argument.token_count()
    return count == 0 and not consume_all


def _option(subject: str, argument: Any, mark: MarkName | None) -> Iterator[Line]:
    names = _positive_names(argument)
    main = names[0]
    default = argument.field_info.default
    if _is_switch(argument):
        shape = "switch" if default is False else f"switch default={_default(default)}"
    else:
        shape = f"{'VALUE...' if _many(argument) else 'VALUE'} default={_default(default)}"
    yield Line(f"{subject} {main} {shape}", mark)
    yield from _spellings(f"{subject} {main}", argument, mark, main=main)


def _spellings(prefix: str, argument: Any, mark: MarkName | None, *, main: str) -> Iterator[Line]:
    for alias in _positive_names(argument):
        if alias != main:
            yield Line(f"{prefix} alias={alias}", mark)
    for negative in argument.negatives:
        yield Line(f"{prefix} negative={negative}", mark)
    yield from _choices(prefix, argument, mark)


def _choices(prefix: str, argument: Any, mark: MarkName | None) -> Iterator[Line]:
    for choice in argument.get_choices(force=True) or ():
        yield Line(f"{prefix} choice={choice_text(choice)}", mark)


def _default(value: object) -> str:
    if value is inspect.Parameter.empty:
        return "unset"
    return default_text(value, none="unset")
