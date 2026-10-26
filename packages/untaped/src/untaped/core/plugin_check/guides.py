"""Guide rules: a plugin's guides and its map lines stay within what an agent can trust.

See ``docs/reference/agent-pages.md#guides``.

Checks the guides of one composed plugin (``PluginSpec.guides``) and the
lines it puts on the map:

- ``guide-name`` — a guide's name (its file stem) is not kebab-case, or is
  the name or alias of one of the plugin's top-level commands, which would
  shadow it;
- ``guide-description`` — a guide has no readable front matter with a
  one-line ``description`` saying when to read it;
- ``guide-heading`` — a guide's text has a level-one heading of its own (the
  page writes it);
- ``guide-link`` — an ``untaped agents …`` the guide quotes names no page
  (another plugin's page is checked only when that plugin is composed);
- ``guide-command`` — another ``untaped …`` command the guide quotes does
  not parse: ``console`` ``$`` lines, ``bash`` blocks and the comments of
  ``yaml`` blocks in full; inline code, in prose or a comment, may leave out
  required arguments;
- ``guide-yaml`` — a block fenced as ```` ```yaml KIND ```` does not validate
  against the model declaring the record kind ``KIND``;
- ``map-line`` — a line the plugin puts on the map (its summary, an
  extension point it owns, its line under one it fills) is over
  :data:`MAP_LINE_LIMIT` characters.

Lines are ``<where>::<rule>::<detail>``, where is ``guides/<file>`` or the
plugin's name; they carry no source line, so no inline marker can suppress
them.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from typing import TYPE_CHECKING

import yaml
from cyclopts import App
from pydantic import ValidationError

from untaped.runtime.command_lines import (
    FENCE,
    argv_of,
    commands,
    console_lines,
    parse_problem,
)
from untaped.runtime.registry import CompositionResult, PluginSpec
from untaped.sdk.errors import UsageError, first_validation_error

if TYPE_CHECKING:
    from untaped.core.agents._guides import Guide

MAP_LINE_LIMIT = 120
"""The longest first line the map shows: each plugin adds a bounded cost to every session."""

_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_KIND_FENCE = re.compile(r"^```yaml[ \t]+(\S+)[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_HEADING = re.compile(r"^# ", re.MULTILINE)


def guide_violations(root: App, result: CompositionResult, spec: PluginSpec) -> list[str]:
    """Every guide and map-line violation of the composed plugin ``spec``."""
    # The pages read this package (the Examples section); import them on use.
    from untaped.core.agents._build import top_names  # noqa: PLC0415
    from untaped.core.agents._guides import SUFFIX, guides  # noqa: PLC0415

    found = list(_map_lines(root, result, spec))
    listed = guides(spec)
    if not listed:
        return found
    top = top_names(None if spec.app_factory is None else root[spec.name])
    for guide in listed:
        where = f"guides/{guide.name}{SUFFIX}"
        found += [
            f"{where}::{rule}::{detail}" for rule, detail in _guide(root, result, spec, guide, top)
        ]
    return found


def _guide(
    root: App, result: CompositionResult, spec: PluginSpec, guide: Guide, top: frozenset[str]
) -> Iterator[tuple[str, str]]:
    if not _KEBAB.match(guide.name):
        yield "guide-name", f"{guide.name!r} is not kebab-case"
    elif guide.name in top:
        yield "guide-name", f"the command `{guide.name}` shadows it; rename the guide"
    if guide.problem is not None:
        yield "guide-description", guide.problem
    if _HEADING.search(FENCE.sub("", guide.body)):
        yield "guide-heading", "the page writes the heading; start the guide's own at ##"
    yield from _quoted(root, result, spec, guide.body)
    yield from _yaml_blocks(result, guide.body)


def _quoted(
    root: App, result: CompositionResult, spec: PluginSpec, body: str
) -> Iterator[tuple[str, str]]:
    quoted = [*((line, False) for line in console_lines(body)), *commands(body)]
    from untaped.core.agents import page  # noqa: PLC0415

    composed = {each.spec.name for each in result.plugins}
    for command, inline in quoted:
        argv, _ = argv_of(command)
        if argv[:1] != ["agents"]:
            problem = parse_problem(root, command, inline=inline)
            if problem is not None:
                yield "guide-command", f"{command}: {problem}"
            continue
        # The page reads a quoted path as its words: read it unquoted, placeholders and all.
        argv, placeholders = argv_of(" ".join(shlex.split(command)))
        words = argv[1:]
        if placeholders & set(words) or (words and words[0] not in composed | {spec.name}):
            continue
        try:
            page(root, result, words)
        except UsageError as exc:
            yield "guide-link", f"{command}: {exc}"


def _yaml_blocks(result: CompositionResult, body: str) -> Iterator[tuple[str, str]]:
    blocks = _KIND_FENCE.findall(body)
    if not blocks:
        return
    from untaped.core.commands.plugins import kind_model  # noqa: PLC0415 - imports kinds lazily

    for kind, block in blocks:
        model = kind_model(kind, result)
        if model is None:
            yield "guide-yaml", f"{kind}: no record kind by that name"
            continue
        try:
            model.model_validate(yaml.safe_load(block))
        except yaml.YAMLError as exc:
            yield "guide-yaml", f"{kind}: not YAML: {' '.join(str(exc).split())}"
        except ValidationError as exc:
            yield "guide-yaml", f"{kind}: {first_validation_error(exc)}"


def _map_lines(root: App, result: CompositionResult, spec: PluginSpec) -> Iterator[str]:
    """The plugin's lines on the map over the limit, measured on the map itself."""
    from untaped.core.agents._build import build_map  # noqa: PLC0415

    shown = build_map(root, result).plugins
    own = next(entry for entry in shown if entry.name == spec.name)
    lines = [("its summary line", own.summary)]
    lines += [("an extension point's line", point.title) for point in own.points]
    lines += [
        (f"its line under {point.owner}'s extension point", fill.summary)
        for entry in shown
        for point in entry.points
        for fill in point.fills
        if fill.plugin == spec.name
    ]
    for what, line in lines:
        if len(line) > MAP_LINE_LIMIT:
            yield (
                f"{spec.name}::map-line::{what} is {len(line)} characters, over "
                f"{MAP_LINE_LIMIT}: every agent session reads the map"
            )
