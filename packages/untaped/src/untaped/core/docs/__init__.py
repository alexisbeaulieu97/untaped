"""``untaped docs``: the documentation of this untaped, rendered from what plugins declare.

Plugins declare, core renders: every page comes from the composed
declaration (:mod:`untaped.runtime.declaration`), which carries the
docstrings and fields of commands, models, bundles and contracts, so no
page imports a plugin module. An address is ``untaped docs``, then a plugin
(``untaped`` for core), a section (``commands``, ``inputs``, ``outputs``,
``settings``, ``extensions``, ``guides``) and a name; each page's title is
its address. ``--search`` prints the matching lines of every page under an
address. A name this untaped lacks is a usage error (exit 2) naming the
page that lists what exists.

This package is imported only when ``untaped docs`` runs, so root
``--help`` never pays for it.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Sequence
from importlib import metadata

from cyclopts import App

from untaped.core.docs import (
    _commands,
    _extensions,
    _guides,
    _models,
    _overview,
    _plugin,
    _settings,
)
from untaped.core.docs._requirement import UntapedRequirement, untaped_requirement
from untaped.core.docs._site import CORE, Site, nodes
from untaped.runtime.declaration import PluginDecl
from untaped.runtime.registry import CompositionResult
from untaped.sdk.errors import UsageError
from untaped.sdk.words import shown_path

type Address = tuple[str, ...]


def page(root: App, result: CompositionResult, words: Sequence[str]) -> str:
    """The page ``untaped docs WORDS...`` prints, as Markdown ending in one newline.

    A quoted address (``untaped docs jira "commands issues get"``) reads as
    the same words unquoted.
    """
    return _text(_lines(Site(result, root), _address(words)))


def search(root: App, result: CompositionResult, words: Sequence[str], text: str) -> str:
    """Every line of the pages under ``words`` containing ``text`` (any case), after its address."""
    site = Site(result, root)
    under = _address(words)
    _lines(site, under)  # an unknown address fails as its page would
    wanted = text.casefold()
    hits = [
        f"{' '.join(('untaped docs', *address))}: {line}"
        for address in addresses(site, under)
        for line in _text(_lines(site, address)).splitlines()
        if wanted in line.casefold()
    ]
    return "".join(f"{hit}\n" for hit in hits)


def _address(words: Sequence[str]) -> Address:
    return tuple(part for word in words for part in word.split())


def _text(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"


def addresses(site: Site, under: Address = ()) -> Iterator[Address]:
    """Every page's address under ``under`` (itself first), depth first."""
    every = [(), *(a for name in site.names() for a in _plugin_addresses(site, name))]
    yield from (address for address in every if address[: len(under)] == under)


def _plugin_addresses(site: Site, name: str) -> Iterator[Address]:
    yield (name,)
    plugin = site.plugin(name)
    if plugin is None:
        if _guides.lines_of(site, name):
            yield (name, "guides")
            yield from ((name, "guides", word) for word, _ in _guides.lines_of(site, name))
        return
    present = _plugin.sections(site, plugin)
    if "commands" in present:
        yield (name, "commands")
        for node in nodes(plugin):
            yield (name, "commands", *node.path[1:])
    for section in _MODELS:
        if section in present:
            yield (name, section)
            yield from (
                (name, section, _models.noun(k)) for k in _models.kinds(site, name, section)
            )
    if "settings" in present:
        yield (name, "settings")
    if "extensions" in present:
        yield (name, "extensions")
        yield from ((name, "extensions", point.name) for point in plugin.points)
    if site.guides(name):
        yield (name, "guides")
        yield from ((name, "guides", guide.name) for guide in site.guides(name))


def _lines(site: Site, address: Address) -> list[str]:
    if not address:
        return _overview.page(site)
    name, rest = address[0], address[1:]
    plugin = site.plugin(name)
    if plugin is None:
        raise UsageError(_not_installed(site, name))
    if not rest:
        return _plugin.page(site, plugin)
    section, words = rest[0], rest[1:]
    # An empty section is hidden, as the plugin's page hides it.
    shown = section in _plugin.sections(site, plugin) or (section == "guides" and site.guides(name))
    found = _section(site, plugin, section, words) if shown else None
    if found is None:
        listed = " ".join(("untaped docs", name, *((section,) if shown else ())))
        raise UsageError(f'no page "{" ".join(address)}"; `{listed}` lists what {name} has')
    return found


_MODELS: tuple[_models.Section, ...] = ("inputs", "outputs")


def _section(site: Site, plugin: PluginDecl, section: str, words: Address) -> list[str] | None:
    name = plugin.name
    if section == "commands":
        if not words:
            return _commands.list_page(site, plugin)
        node = _commands.find(plugin, words)
        return None if node is None else _commands.page(site, plugin, node)
    models = next((each for each in _MODELS if each == section), None)
    if models is not None:
        if not words:
            return _models.list_page(site, name, models)
        kind = _models.find(site, name, models, words[0]) if len(words) == 1 else None
        return None if kind is None else _models.page(site, kind, models)
    if section == "settings" and not words:
        return _settings.page(site, name)
    if section == "extensions":
        if not words:
            return _extensions.list_page(site, plugin)
        point = _extensions.find(plugin, words[0]) if len(words) == 1 else None
        return None if point is None else _extensions.page(site, plugin, point)
    if section == "guides":
        if not words:
            return _guides.list_page(site, name)
        return _guides.page(site, name, words[0]) if len(words) == 1 else None
    return None


def _not_installed(site: Site, name: str) -> str:
    record = next((each for each in site.result.quarantine if each.name == name), None)
    if record is not None:
        return (
            f"{name} is installed but was quarantined [{record.reason}]: {record.detail}; "
            "`untaped plugin list` shows it"
        )
    return (
        f"{name} is not installed in this untaped ({metadata.version(CORE)}, "
        f"{shown_path(sys.prefix)}); `untaped docs` lists what is"
    )


__all__ = ["UntapedRequirement", "addresses", "page", "search", "untaped_requirement"]
