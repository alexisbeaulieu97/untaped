"""Guides: the prose a reader must not miss, from the bundles and enums commands take.

A guide is the docstring of a bundle (its options listed after the prose)
or of an enum (its choices listed); every command taking one links it
"Read first", and the plugin's page lists them all.
"""

from __future__ import annotations

from untaped.core.docs._commands import option_line
from untaped.core.docs._docstring import describe, markdown
from untaped.core.docs._site import ORGANIZED, Site
from untaped.runtime.declaration import GuideDecl


def lines_of(site: Site, name: str) -> list[tuple[str, str]]:
    """Each guide under plugin ``name``: its address word and its line."""
    return [(guide.name, markdown(guide.summary)) for guide in site.guides(name)]


def read_first(site: Site, name: str) -> list[str]:
    """The plugin page's ``## Read first``: each guide's address and line."""
    found = lines_of(site, name)
    if not found:
        return []
    return [
        "",
        "## Read first",
        *(f"- `untaped docs {name} guides {word}`: {line}" for word, line in found),
    ]


def list_page(site: Site, name: str) -> list[str]:
    """``untaped docs PLUGIN guides``: the guides, as the plugin's page lists them."""
    lines = [*site.header(f"{name} guides", name), ORGANIZED, ""]
    lines.append(f"What {name}'s commands share. One guide: `untaped docs {name} guides NAME`.")
    lines += [""] + [f"- `{word}`: {line}" for word, line in lines_of(site, name)]
    return lines


def page(site: Site, name: str, word: str) -> list[str] | None:
    """One guide's page; ``None`` when plugin ``name`` has no guide ``word``."""
    guide = next((g for g in site.guides(name) if g.name == word), None)
    return None if guide is None else _page(site, guide)


def _page(site: Site, guide: GuideDecl) -> list[str]:
    lines = [*site.header(f"{guide.plugin} guides {guide.name}", guide.plugin), ORGANIZED]
    described = describe(guide.doc)
    lines += [part for text in (described.summary, described.body) if text for part in ("", text)]
    if guide.options:
        lines += ["", "## Options", "", *(option_line(o) for o in guide.options if o.shown)]
    if guide.choices:
        lines += ["", "## Choices", "", *(f"- `{choice}`" for choice in guide.choices)]
    if described.examples:
        lines += ["", "## Examples", "", described.examples]
    return lines
