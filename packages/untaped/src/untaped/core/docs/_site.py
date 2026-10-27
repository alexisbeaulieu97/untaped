"""The pages' one view of this untaped: the declaration, the plugins it composed, who answered.

Every page reads :class:`Site`, never a plugin class: the declaration
carries the prose of commands, kinds, guides and extension points
(:mod:`untaped.runtime.declaration`). A plugin still in the 11.x shape
(no plugin class yet) is a *seam* plugin: the site knows its help line and
its guide files only, until its move (the old-mechanisms sweep removes the
seam with the 11.x reader).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from cyclopts import App

from untaped.core.docs._docstring import first_line
from untaped.core.docs._requirement import untaped_requirement
from untaped.runtime.declaration import (
    CommandDecl,
    Declaration,
    GroupDecl,
    GuideDecl,
    KindDecl,
    PluginDecl,
    PointDecl,
)
from untaped.runtime.registry import CompositionResult, RegisteredPlugin
from untaped.sdk.words import shown_path

#: The name core answers to (``untaped docs untaped``); no plugin may take it.
CORE = "untaped"
#: The line every page but the overview carries under its header.
ORGANIZED = "How these pages are organized: `untaped docs`."


@dataclass(frozen=True)
class Site:
    """What the pages are generated from: one composition, read once per invocation."""

    result: CompositionResult
    #: The root app, whose own options are the ones every command takes.
    root: App
    _kinds: dict[str, KindDecl] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_kinds", {kind.kind: kind for kind in self.declaration.kinds})

    @property
    def declaration(self) -> Declaration:
        return self.result.mount.declaration

    # --- plugins -------------------------------------------------------------------

    def names(self) -> list[str]:
        """Every composed plugin's name: core first, then by name."""
        ordered = sorted(self.result.plugins, key=lambda each: (not each.root, each.spec.name))
        return [each.spec.name for each in ordered]

    def plugin(self, name: str) -> PluginDecl | None:
        """The composed 12.0 plugin ``name``; ``None`` for a seam plugin or none."""
        return next((each for each in self.declaration.plugins if each.name == name), None)

    def registered(self, name: str) -> RegisteredPlugin | None:
        return next((each for each in self.result.plugins if each.spec.name == name), None)

    def seam(self, name: str) -> RegisteredPlugin | None:
        """The composed 11.x plugin ``name``, which has no declaration yet."""
        found = self.registered(name)
        return found if found is not None and self.plugin(name) is None else None

    def summary(self, name: str) -> str:
        """A plugin's help line."""
        decl = self.plugin(name)
        if decl is not None:
            return first_line(decl.summary)
        spec = self.result_plugin(name).spec
        if spec.help is not None:
            return first_line(spec.help)
        app = self.result_plugin(name).app
        return "" if app is None else first_line(app.help)

    def result_plugin(self, name: str) -> RegisteredPlugin:
        found = self.registered(name)
        if found is None:
            raise LookupError(name)
        return found

    # --- what a plugin's pages list ----------------------------------------------------

    def commands(self, plugin: PluginDecl) -> list[CommandDecl]:
        """The plugin's commands, depth first, its own (``runs``) first."""
        return [c for c in self.declaration.commands() if c.path[0] == plugin.name]

    def guides(self, name: str) -> list[GuideDecl]:
        """The guides living under ``name``: those of its classes, whichever plugin links them."""
        found: dict[str, GuideDecl] = {}
        for plugin in self.declaration.plugins:
            for guide in plugin.guides:
                if guide.plugin == name:
                    found.setdefault(guide.target, guide)
        return sorted(found.values(), key=lambda guide: guide.name)

    def guide(self, target: str) -> GuideDecl | None:
        """The guide of the class ``target``."""
        return next(
            (g for p in self.declaration.plugins for g in p.guides if g.target == target), None
        )

    def kind(self, kind: str) -> KindDecl | None:
        return self._kinds.get(kind)

    def kind_of_target(self, target: str) -> KindDecl | None:
        return next((kind for kind in self.declaration.kinds if kind.target == target), None)

    def inputs(self, name: str) -> list[KindDecl]:
        """The files ``name``'s kinds describe that a command reads or writes (``File[M]``)."""
        wanted = {k for c in self.declaration.commands() for k in (*c.inputs, *c.writes)}
        return self._owned(name, wanted)

    def outputs(self, name: str) -> list[KindDecl]:
        """The rows of ``name``'s kinds that a command prints (``Output[R]``)."""
        wanted = {c.output for c in self.declaration.commands() if c.output is not None}
        return self._owned(name, wanted)

    def _owned(self, name: str, wanted: set[str]) -> list[KindDecl]:
        return [
            kind for kind in self.declaration.kinds if kind.plugin == name and kind.kind in wanted
        ]

    def readers(self, kind: str) -> list[CommandDecl]:
        return [c for c in self.declaration.commands() if kind in c.inputs]

    def writers(self, kind: str) -> list[CommandDecl]:
        return [c for c in self.declaration.commands() if kind in c.writes]

    def printers(self, kind: str) -> list[CommandDecl]:
        return [c for c in self.declaration.commands() if c.output == kind]

    def point(self, target: str) -> tuple[PluginDecl, PointDecl] | None:
        """The plugin offering the contract ``target``, and its point."""
        for plugin in self.declaration.plugins:
            for point in plugin.points:
                if point.target == target:
                    return plugin, point
        return None

    def fillers(self, target: str) -> list[tuple[str, str]]:
        """Each plugin filling the contract ``target``, with its provider's line."""
        return sorted(
            (plugin.name, fill.summary)
            for plugin in self.declaration.plugins
            for fill in plugin.fills
            if fill.contract == target
        )

    def line(self, command: CommandDecl) -> str:
        """The command line ``command`` is typed as: ``untaped hello say``."""
        return " ".join(("untaped", *self.declaration.words(command)))

    # --- the header -----------------------------------------------------------------

    def header(self, title: str, name: str | None = None) -> list[str]:
        """A page's title, the line naming who answered, and the project mismatch line."""
        version = metadata.version(CORE)
        parts = [f"untaped {version}"]
        registered = None if name is None or name == CORE else self.registered(name)
        if registered is not None:
            ref = registered.plugin_ref
            distribution = " ".join(
                part for part in (ref.distribution, ref.distribution_version) if part
            )
            if distribution:
                parts.append(distribution)
        parts.append(shown_path(sys.prefix))
        lines = [f"# {title}", " · ".join(parts)]
        mismatch = _mismatch(version)
        if mismatch is not None:
            lines.append(mismatch)
        return lines


def nodes(plugin: PluginDecl) -> Iterator[CommandDecl | GroupDecl]:
    """Every group and command under ``plugin``, depth first."""

    def walk(children: tuple[CommandDecl | GroupDecl, ...]) -> Iterator[CommandDecl | GroupDecl]:
        for node in children:
            yield node
            if isinstance(node, GroupDecl):
                yield from walk(node.commands)

    yield from walk(plugin.commands)


def _mismatch(version: str) -> str | None:
    found = untaped_requirement(Path.cwd())
    if found is None or found.satisfied_by(version):
        return None
    return (
        f"This project ({shown_path(found.file.parent)}) requires {found.requirement}; "
        f"this untaped is {version}. Run the project's own untaped, through the project's "
        "environment, instead."
    )
