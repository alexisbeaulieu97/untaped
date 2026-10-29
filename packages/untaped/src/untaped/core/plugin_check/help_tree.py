"""Help-tree rules: every command follows the command grammar.

See ``docs/reference/conventions.md#options``.

Checks each visible command of a command subtree. What a command class
declares can't break the parameter rules (help, negatives, positionals and
duplicate flags are composition's), so these are about names and prose:

- ``command-name`` — a command or group name is not kebab-case;
- ``reserved-short`` — a reserved short flag means something else;
- ``ambiguous-short`` — a non-reserved short flag has two meanings within
  the checked subtrees;
- ``reserved-panel`` — a command or option group named after one of core's
  panels (Plugins, Experimental, Deprecated, Deprecated options, Global
  options) that is not core's own: a same-named group would merge into it;
- ``unkeyed-panel`` — a shown group of the plugin's own without a sort key
  below 100, which would list after Global options.
- ``example-command`` — a command line in a command docstring's
  ``Examples`` section (a ``$ untaped …`` line of a ``console`` fence) that
  does not parse against the composed root.

Lines are ``<command path>::<rule>::<detail>``; they carry no source line, so
no inline marker can suppress them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from itertools import chain

from cyclopts import App, Group

from untaped.runtime.command_lines import console_lines, examples_of, parse_problem, segments
from untaped.runtime.help_panels import PLUGIN_PANEL_KEY_LIMIT, RESERVED_PANELS, is_core_panel

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


def example_violations(root: App, app: App) -> Iterator[tuple[str, str]]:
    """Yield ``(rule, detail)`` for each example command of ``app`` that doesn't parse."""
    if app.default_command is None:
        return
    for line in console_lines(examples_of(app.default_command)):
        for command in segments(line):
            problem = parse_problem(root, command, inline=False)
            if problem is not None:
                yield "example-command", f"{command}: {problem}"


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

    The groups its help can render count: its own ``group``, its
    ``group_commands`` when it lists subcommands, and each shown argument's
    resolved group (which carries ``group_parameters``/``group_arguments``,
    own or inherited). Core's panels pass by identity, so the Plugins panel core
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
    yield from as_groups(app.group)
    # Apps inherit their parent's group_* defaults; only what they list counts.
    if next(_subcommands(app), None) is not None:
        yield from as_groups(app.group_commands)
    if app.default_command is not None:
        for argument in app.assemble_argument_collection(parse_docstring=True):
            if argument.show and argument.parse:  # a hidden option lists in no panel
                yield from as_groups(argument.parameter.group)


def as_groups(value: Group | str | Iterable[Group | str] | None) -> Iterator[Group | str]:
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
    """Whether ``group`` has a plain numeric sort key below the plugin limit.

    A tuple key (``Group.create_ordered``) is not one: cyclopts sorts it among
    core's int keys and help fails comparing the two.
    """
    key = group.sort_key if isinstance(group, Group) else None
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
            for rule, detail in chain(
                command_violations(path, app), panel_violations(app), example_violations(root, app)
            ):
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
