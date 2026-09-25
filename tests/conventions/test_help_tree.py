"""Help-tree lint: every command follows the command-grammar conventions.

Walks the fully composed built-in tree (``build_root_app(externals=[])``)
and checks each visible command against ``docs/conventions.md``:

- ``missing-help`` — a parameter (positional or option) has no help text;
- ``duplicate-option`` — two parameters of one command answer to one name;
- ``empty-negative`` — an ``--empty-*`` negative flag is exposed;
- ``needless-negative`` — a ``--no-*`` flag for a boolean that defaults to
  false (declare it with ``negative=""``);
- ``positional-or-keyword`` — a parameter can be passed both ways (options
  must never be positional);
- ``command-name`` — a command or group name is not kebab-case;
- ``verb`` — a leaf verb is outside the closed verb set;
- ``reserved-short`` — a reserved short flag means something else;
- ``ambiguous-short`` — a non-reserved short flag has two meanings;
- ``mutation-format`` — a mutation verb has no ``--format``;
- ``destructive-controls`` — a destructive verb lacks ``--yes``/``--dry-run``.

Existing violations live in ``baselines/help_tree/<owner>.txt``.
"""

from __future__ import annotations

import inspect
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from typing import Any

from cyclopts import App

from untaped.bootstrap import build_root_app

ROOT_COMMANDS = frozenset({"config", "profile", "skills", "doctor", "capabilities"})

VERBS = frozenset(
    {
        # read
        "list",
        "get",
        "status",
        "whoami",
        "ping",
        # write
        "create",
        "set",
        "unset",
        "add",
        "remove",
        "delete",
        "prune",
        "edit",
        "patch",
        "apply",
        "copy",
        # update
        "sync",
        "refresh",
        # other
        "export",
        "init",
        "run",
        "launch",
        "wait",
        "validate",
        "test",
        "cancel",
        "relaunch",
    }
)
MUTATION_VERBS = frozenset(
    {"create", "set", "unset", "add", "remove", "delete", "prune", "patch", "apply", "copy"}
)
DESTRUCTIVE_VERBS = frozenset({"delete", "remove", "prune", "cancel"})
RESERVED_SHORTS = {
    "-f": "--format",
    "-c": "--columns",
    "-y": "--yes",
    "-j": "--parallel",
    "-o": "--out",
    "-i": "--ignore-case",
    "-r": "--repo",
    "-w": "--workspace",
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


def _owner(path: tuple[str, ...]) -> str:
    return "root" if path[0] in ROOT_COMMANDS else path[0]


def _long_name(names: tuple[str, ...]) -> str:
    return next((name for name in names if name.startswith("--")), names[0])


def _command_violations(path: tuple[str, ...], app: App) -> Iterator[tuple[str, str]]:
    """Yield ``(rule, detail)`` for one command (group names and leaves)."""
    name = path[-1]
    if not _KEBAB.match(name):
        yield "command-name", name
    if app.default_command is None:
        return
    has_subcommands = any(True for _ in _subcommands(app))
    if not has_subcommands and name not in VERBS and path[0] not in ROOT_COMMANDS:
        yield "verb", name
    arguments = [
        argument
        for argument in app.assemble_argument_collection(parse_docstring=True)
        if argument.show and argument.parse
    ]
    yield from _argument_violations(arguments)
    options = {flag for argument in arguments for flag in argument.names}
    if name in MUTATION_VERBS and "--format" not in options:
        yield "mutation-format", name
    if name in DESTRUCTIVE_VERBS:
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


def _shorts(app: App) -> Iterator[tuple[str, str]]:
    for argument in app.assemble_argument_collection(parse_docstring=True):
        if not (argument.show and argument.parse):
            continue
        names = tuple(argument.names)
        long_name = _long_name(names)
        for flag in names:
            if re.fullmatch(r"-[A-Za-z0-9]", flag):
                yield flag, long_name


def collect_violations() -> dict[str, list[str]]:
    """``{owner: ["<command path>::<rule>::<detail>", ...]}`` for the built-in tree."""
    root = build_root_app(externals=[])
    found: dict[str, list[str]] = defaultdict(list)
    shorts: dict[str, dict[str, list[tuple[str, ...]]]] = defaultdict(lambda: defaultdict(list))
    for path, app in _walk(root, ()):
        command = " ".join(path)
        for rule, detail in _command_violations(path, app):
            found[_owner(path)].append(f"{command}::{rule}::{detail}")
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
                found[_owner(path)].append(f"{' '.join(path)}::{rule}::{flag} {long_name}")
    return found


def test_help_tree_follows_the_command_grammar(baseline: Any) -> None:
    baseline("help_tree", collect_violations())
