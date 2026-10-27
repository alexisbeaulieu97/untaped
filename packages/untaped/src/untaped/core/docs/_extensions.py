"""The ``extensions`` section: the extension points a plugin offers, and how to fill one.

An extension point is a contract a plugin ``offers``; another plugin fills
it by naming a provider class in its ``fills``. A point's page is the
developer reference: the contract's docstring (how to fill it), who fills
it, its methods and the model it takes and returns.
"""

from __future__ import annotations

from untaped.core.docs._commands import mark
from untaped.core.docs._docstring import describe, markdown
from untaped.core.docs._models import address as kind_address
from untaped.core.docs._models import tables
from untaped.core.docs._site import ORGANIZED, Site
from untaped.runtime.declaration import PluginDecl, PointDecl


def find(plugin: PluginDecl, word: str) -> PointDecl | None:
    return next((point for point in plugin.points if point.name == word), None)


def list_page(site: Site, plugin: PluginDecl) -> list[str]:
    """``untaped docs PLUGIN extensions``: each point with who fills it."""
    name = plugin.name
    lines = [
        *site.header(f"{name} extensions{mark(plugin.stability)}", name),
        ORGANIZED,
        "",
        f"Extension points {name} offers other plugins. "
        f"One point: `untaped docs {name} extensions NAME`.",
        "",
    ]
    for point in plugin.points:
        lines.append(
            f"- `{point.name}`{mark(point.stability, plugin.stability)}: {markdown(point.summary)}"
        )
        fillers = site.fillers(point.target)
        filled = ", ".join(each for each, _ in fillers) if fillers else "no installed plugin"
        lines.append(f"  Filled by: {filled}.")
    return lines


def page(site: Site, plugin: PluginDecl, point: PointDecl) -> list[str]:
    """One point's page: how to fill it, who fills it, its methods and types."""
    title = f"{plugin.name} extensions {point.name}{mark(point.stability or plugin.stability)}"
    lines = [*site.header(title, plugin.name), ORGANIZED]
    described = describe(point.doc)
    lines += ["", described.summary] if described.summary else []
    lines += ["", "## Filled by"]
    fillers = site.fillers(point.target)
    lines += [
        f"- {each}: {markdown(summary)}" if summary else f"- {each}" for each, summary in fillers
    ] or ["- No installed plugin fills it."]
    if described.body:
        lines += ["", "## How to fill it", "", described.body]
    if point.methods:
        lines += ["", "## Methods", ""]
        for method in point.methods:
            need = "required" if method.required else "optional"
            text = f": {markdown(method.summary)}" if method.summary else ""
            lines.append(f"- `{method.signature}` ({need}){text}")
    kind = None if point.item is None else site.kind(point.item)
    if kind is not None:
        lines += ["", "## Types", "", f"`{kind.kind}`: {markdown(kind.summary)}"]
        if site.printers(kind.kind):
            lines.append(f"Its page: `{kind_address(kind, 'outputs')}`.")
        lines += tables(kind.fields)
    if described.examples:
        lines += ["", "## Examples", "", described.examples]
    return lines
