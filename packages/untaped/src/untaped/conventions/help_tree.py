"""Help-tree rules: every command follows the command grammar.

See ``docs/reference/conventions.md#options``.

Checks each visible command of a command subtree:

- ``missing-help`` — a parameter (positional or option) has no help text;
- ``duplicate-option`` — two parameters of one command answer to one name;
- ``empty-negative`` — an ``--empty-*`` negative flag is exposed;
- ``needless-negative`` — a ``--no-*`` flag for a boolean that defaults to
  false (declare it with ``negative=""``);
- ``positional-or-keyword`` — a parameter can be passed both ways (options
  must never be positional);
- ``command-name`` — a command or group name is not kebab-case;
- ``reserved-short`` — a reserved short flag means something else;
- ``ambiguous-short`` — a non-reserved short flag has two meanings within
  the checked subtrees;
- ``undeclared-write`` — a command exposes ``--yes``/``--dry-run`` without
  declaring ``@writes``;
- ``mutation-format`` — a declared write has no ``--format``;
- ``destructive-controls`` — a destructive command lacks ``--yes``/``--dry-run``;
- ``reserved-panel`` — a command or option group named after one of core's
  panels (Plugins, Experimental, Deprecated, Global options) that is not
  core's own: a same-named group would merge into it;
- ``unkeyed-panel`` — a shown group of the plugin's own without a sort key
  below 100, which would list after Global options.

Lines are ``<command path>::<rule>::<detail>``; they carry no source line, so
no inline marker can suppress them.
"""

from __future__ import annotations

import inspect
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from itertools import chain
from typing import Any

from cyclopts import App, Group

from untaped.cli import write_kind
from untaped.help_panels import PLUGIN_PANEL_KEY_LIMIT, RESERVED_PANELS, is_core_panel

RESERVED_SHORTS = {
    "-f": "--format",
    "-c": "--columns",
    "-y": "--yes",
    "-j": "--parallel",
    "-o": "--out",
    "-i": "--ignore-case",
    "-r": "--repo",
    "-v": "--verbose",
    "-q": "--quiet",
    "-h": "--help",
}
_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _subcommands(app: App) -> Iterator[tuple[str, App]]:
    for name in app:
        if name.startswith("-"):
            continue
        sub = app[name]
        if sub.show is False:
            continue
        yield name, sub


def _walk(app: App, path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], App]]:
    for name, sub in _subcommands(app):
        yield (*path, name), sub
        yield from _walk(sub, (*path, name))


def _long_name(names: tuple[str, ...]) -> str:
    return next((name for name in names if name.startswith("--")), names[0])


def command_violations(path: tuple[str, ...], app: App) -> Iterator[tuple[str, str]]:
    """Yield ``(rule, detail)`` for one command (group names and leaves)."""
    name = path[-1]
    if not _KEBAB.match(name):
        yield "command-name", name
    if app.default_command is None:
        return
    arguments = [
        argument
        for argument in app.assemble_argument_collection(parse_docstring=True)
        if argument.show and argument.parse
    ]
    yield from _argument_violations(arguments)
    options = {flag for argument in arguments for flag in argument.names}
    kind = write_kind(app.default_command)
    if kind is None and options & {"--yes", "--dry-run"}:
        yield "undeclared-write", " ".join(sorted(options & {"--yes", "--dry-run"}))
    if kind is not None and "--format" not in options:
        yield "mutation-format", name
    if kind == "destructive":
        for flag in ("--yes", "--dry-run"):
            if flag not in options:
                yield "destructive-controls", flag


def _argument_violations(arguments: list[Any]) -> Iterator[tuple[str, str]]:
    seen: Counter[str] = Counter()
    for argument in arguments:
        names = tuple(argument.names)
        label = _long_name(names) if names else argument.field_info.name
        if not argument.parameter.help:
            yield "missing-help", label
        for flag in names:
            if flag.startswith("--empty-"):
                yield "empty-negative", flag
            elif flag.startswith("--no-") and argument.field_info.default is False:
                yield "needless-negative", flag
        if argument.field_info.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
            yield "positional-or-keyword", label
        seen.update(names)
    for flag, count in sorted(seen.items()):
        if count > 1:
            yield "duplicate-option", flag


#: cyclopts' own Commands, Parameters and Arguments panels, by their sort-key markers.
_DEFAULT_KEYS = tuple(
    group.sort_key
    for group in (
        Group.create_default_commands(),
        Group.create_default_parameters(),
        Group.create_default_arguments(),
    )
)
_DEFAULT_NAMES = frozenset({"Commands", "Parameters", "Arguments"})


def panel_violations(app: App) -> Iterator[tuple[str, str]]:
    """Yield ``(rule, detail)`` for the help panels ``app`` names.

    Every group an app can name counts: its own ``group``, its
    ``group_commands``/``group_parameters``/``group_arguments`` and each
    argument's group. Core's panels pass by identity, so the Plugins panel core
    puts a plugin's top app in is never the plugin's mistake.
    """
    seen: set[str] = set()
    for group in _named_groups(app):
        name = str(group.name if isinstance(group, Group) else group)
        if name in seen or is_core_panel(group):
            continue
        seen.add(name)
        if name in RESERVED_PANELS:
            yield "reserved-panel", name
        elif not _default_or_hidden(group) and not _keyed(group):
            yield (
                "unkeyed-panel",
                f"{name}: give it a sort_key below {PLUGIN_PANEL_KEY_LIMIT} "
                "so Global options stays the last panel",
            )


def _named_groups(app: App) -> Iterator[Group | str]:
    # A leaf inherits its parent's group_commands but lists no commands in it.
    commands = app.group_commands if any(True for _ in _subcommands(app)) else None
    for attribute in (app.group, commands, app.group_parameters, app.group_arguments):
        yield from _as_groups(attribute)
    if app.default_command is not None:
        for argument in app.assemble_argument_collection(parse_docstring=True):
            if argument.show and argument.parse:  # a hidden option lists in no panel
                yield from _as_groups(argument.parameter.group)


def _as_groups(value: Group | str | Iterable[Group | str] | None) -> Iterator[Group | str]:
    if value is None:
        return
    if isinstance(value, Group | str):
        yield value
    else:
        yield from value


def _default_or_hidden(group: Group | str) -> bool:
    if isinstance(group, str):
        return group in _DEFAULT_NAMES
    return group.show is False or any(group.sort_key is key for key in _DEFAULT_KEYS)


def _keyed(group: Group | str) -> bool:
    """Whether ``group`` has a numeric sort key below the plugin limit."""
    key = group.sort_key if isinstance(group, Group) else None
    if isinstance(key, tuple) and key:  # Group.create_ordered(sort_key=n) stores (n, counter)
        key = key[0]
    if isinstance(key, bool) or not isinstance(key, int | float):
        return False
    return key < PLUGIN_PANEL_KEY_LIMIT


def _shorts(app: App) -> Iterator[tuple[str, str]]:
    for argument in app.assemble_argument_collection(parse_docstring=True):
        if not (argument.show and argument.parse):
            continue
        names = tuple(argument.names)
        long_name = _long_name(names)
        for flag in names:
            if re.fullmatch(r"-[A-Za-z0-9]", flag):
                yield flag, long_name


def help_tree_violations(root: App, names: Iterable[str]) -> list[str]:
    """``["<command path>::<rule>::<detail>", ...]`` for the subtrees ``root[name]``."""
    found: list[str] = []
    shorts: dict[str, dict[str, list[tuple[str, ...]]]] = defaultdict(lambda: defaultdict(list))
    for top in names:
        subtree = root[top]
        for path, app in [((top,), subtree), *_walk(subtree, (top,))]:
            command = " ".join(path)
            for rule, detail in chain(command_violations(path, app), panel_violations(app)):
                found.append(f"{command}::{rule}::{detail}")
            if app.default_command is not None:
                for flag, long_name in _shorts(app):
                    shorts[flag][long_name].append(path)
    for flag, meanings in sorted(shorts.items()):
        reserved = RESERVED_SHORTS.get(flag)
        for long_name, paths in sorted(meanings.items()):
            if reserved is not None and long_name != reserved:
                rule = "reserved-short"
            elif reserved is None and len(meanings) > 1:
                rule = "ambiguous-short"
            else:
                continue
            for path in paths:
                found.append(f"{' '.join(path)}::{rule}::{flag} {long_name}")
    return found
