"""Guide rules: a plugin's guides and its overview lines stay within what an agent can trust.

See ``docs/reference/docs.md#guides``.

Checks a plugin class's guides (the docstrings of the bundles and enums it
defines, named by the class's target) and the lines it puts on the overview
(``untaped docs``):

- ``guide-link`` — an ``untaped docs …`` the guide quotes names no page
  (another plugin's page is checked only when that plugin is composed);
- ``guide-command`` — another ``untaped …`` command the guide quotes does
  not parse: ``console`` ``$`` lines, ``bash`` blocks and the comments of
  ``yaml`` blocks in full; inline code, in prose or a comment, may leave out
  required arguments;
- ``guide-yaml`` — a block fenced as ```` ```yaml KIND ```` does not validate
  against the model declaring the record kind ``KIND``;
- ``map-line`` — a line the plugin puts on the overview (its summary, its
  line under an extension point it fills) or an extension point it owns
  is over :data:`MAP_LINE_LIMIT` characters.

Lines are ``<where>::<rule>::<detail>``, where is the guide's target or the
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
    argv_of,
    commands,
    console_lines,
    parse_problem,
)
from untaped.runtime.registry import CompositionResult
from untaped.sdk.errors import UsageError, first_validation_error

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin
    from untaped.runtime.declaration import GuideDecl

MAP_LINE_LIMIT = 120
"""The longest line the overview shows: each plugin adds a bounded cost to every session."""

_KIND_FENCE = re.compile(r"^```yaml[ \t]+(\S+)[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def guide_violations(root: App, result: CompositionResult, spec: ComposedPlugin) -> list[str]:
    """Every guide and map-line violation of the composed plugin ``spec``."""
    found = list(_map_lines(root, result, spec))
    for decl in _guide_decls(result, spec.name):
        quoted = [*_quoted(root, result, spec, decl.doc), *_yaml_blocks(result, decl.doc)]
        found += [f"{decl.target}::{rule}::{detail}" for rule, detail in quoted]
    return found


def _guide_decls(result: CompositionResult, name: str) -> tuple[GuideDecl, ...]:
    """The guides plugin class ``name`` defines: its bundles' and enums' docstrings.

    A guide is recorded under each plugin whose commands use it, so one
    defined in ``name``'s package and used only by another plugin is found there.
    """
    found: dict[str, GuideDecl] = {}
    for plugin in result.mount.declaration.plugins:
        for guide in plugin.guides:
            if guide.plugin == name:
                found.setdefault(guide.target, guide)
    return tuple(found.values())


def _quoted(
    root: App, result: CompositionResult, spec: ComposedPlugin, body: str
) -> Iterator[tuple[str, str]]:
    quoted = [*((line, False) for line in console_lines(body)), *commands(body)]
    from untaped.core.docs import page  # noqa: PLC0415

    composed = {each.spec.name for each in result.plugins}
    for command, inline in quoted:
        argv, _ = argv_of(command)
        if argv[:1] != ["docs"]:
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


def _map_lines(root: App, result: CompositionResult, spec: ComposedPlugin) -> Iterator[str]:
    """The plugin's lines on the overview over the limit, as the overview reads them."""
    from untaped.core.docs._site import Site  # noqa: PLC0415

    site = Site(result, root)
    lines = [("its summary line", site.summary(spec.name))]
    plugin = site.plugin(spec.name)
    if plugin is not None:
        lines += [("an extension point's line", point.summary) for point in plugin.points]
        lines += [
            (f"its line under {found[0].name}'s extension point", fill.summary)
            for fill in plugin.fills
            if (found := site.point(fill.contract)) is not None
        ]
    for what, line in lines:
        if len(line) > MAP_LINE_LIMIT:
            yield (
                f"{spec.name}::map-line::{what} is {len(line)} characters, over "
                f"{MAP_LINE_LIMIT}: every agent session reads the overview"
            )
