"""A plugin's page: its description, the guides to read first, its sections, what it extends.

Sections with nothing in them are left out; the guides are listed under
"Read first" rather than as a section (``untaped docs PLUGIN guides`` lists
them again). A plugin not yet in the 12.0
shape (a seam plugin) has its help line, its guides, and a line sending the
reader to its ``--help``, until its move.
"""

from __future__ import annotations

from untaped.core.docs import _commands, _guides, _settings
from untaped.core.docs._commands import mark
from untaped.core.docs._docstring import describe, markdown
from untaped.core.docs._site import ORGANIZED, Site
from untaped.runtime.declaration import PluginDecl


def sections(site: Site, plugin: PluginDecl) -> dict[str, int]:
    """Each section ``plugin`` has, with how many entries it lists."""
    name = plugin.name
    counts = {
        "commands": _commands.count(plugin),
        "inputs": len(site.inputs(name)),
        "outputs": len(site.outputs(name)),
        "settings": len(_settings.settings(site, name)),
        "extensions": len(plugin.points),
    }
    return {section: count for section, count in counts.items() if count}


def _phrase(name: str, section: str) -> str:
    return {
        "commands": "what you can run",
        "inputs": f"the files you write for {name} commands",
        "outputs": f"the rows {name} commands print",
        "settings": f"the `{name}:` section of config.yml",
        "extensions": f"the extension points {name} offers other plugins",
    }[section]


def page(site: Site, plugin: PluginDecl) -> list[str]:
    """``untaped docs PLUGIN``."""
    name = plugin.name
    lines = [*site.header(f"{name}{mark(plugin.stability)}", name), ORGANIZED]
    described = describe(plugin.doc)
    lines += [part for text in (described.summary, described.body) if text for part in ("", text)]
    lines += _guides.read_first(site, name)
    lines += ["", "## Sections"]
    lines += [
        f"- `{section}` ({count}): {_phrase(name, section)}. `untaped docs {name} {section}`"
        for section, count in sections(site, plugin).items()
    ]
    lines += extends(site, plugin, heading=True)
    examples = described.examples
    if plugin.runs is not None:  # the plugin's own command (`untaped lint`): its usage here
        ran = describe(plugin.runs.doc)
        lines += ["", f"## `{site.line(plugin.runs)}`"]
        lines += [part for text in (ran.summary, ran.body) if text for part in ("", text)]
        body = _commands.command_body(site, plugin.runs)
        lines += [f"#{line}" if line.startswith("##") else line for line in body]
        examples = ran.examples or examples
    if examples:
        lines += ["", "## Examples", "", examples]
    return lines


def extends(site: Site, plugin: PluginDecl, *, heading: bool) -> list[str]:
    """``plugin``'s fills: each point's owner, name and line, then the provider's line."""
    lines: list[str] = []
    for fill in plugin.fills:
        found = site.point(fill.contract)
        if found is None:
            continue
        owner, point = found
        text = f"{owner.name} `{point.name}`: {markdown(point.summary)}"
        if fill.summary:
            text += f" ({markdown(fill.summary)})"
        lines.append(f"- {text}" if heading else f"Extends {text}")
    return ["", "## Extends", *lines] if heading and lines else lines


def seam_page(site: Site, name: str) -> list[str]:
    """The page of a plugin not yet in the 12.0 shape: its line, guides and ``--help``."""
    spec = site.result_plugin(name).spec
    lines = [*site.header(f"{name}{' [experimental]' if spec.stability else ''}", name), ORGANIZED]
    summary = site.summary(name)
    lines += ["", summary] if summary else []
    lines += _guides.read_first(site, name)
    return [*lines, "", "## Sections", f"- commands: `untaped {name} --help`"]
