"""``server-api`` and ``adapter`` lines: a plugin's server APIs and the servers each serves.

``server-api <plugin>.<name>  override=<key>`` promises the block and the
setting that overrides detection; ``adapter <plugin>.<name> <Class>`` one
line per adapter, identified by class, so dropping support for a server is a
removed line. An adapter's line carries its own mark (a deprecated one its
major and replacement, which the release check holds it to), else the
block's, else the plugin's. Read from the declaration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.core.surface._lines import DeprecatedMark, Line, LineMark

if TYPE_CHECKING:
    from untaped.runtime.declaration import PluginDecl, StabilityDecl


def server_api_lines(name: str, plugin: PluginDecl, inherited: LineMark | None) -> list[Line]:
    """The lines of ``plugin``'s server APIs, composed as ``name`` with the mark ``inherited``."""
    lines: list[Line] = []
    for block in plugin.blocks:
        api = block.server_api
        if api is None:
            continue
        subject = f"{name}.{block.name}"
        mark = _mark(api.stability) or inherited
        override = f"{name}.{api.override}"
        lines.append(Line(f"server-api {subject} override={override}", mark))
        lines += [
            Line(f"adapter {subject} {adapter.name}", _mark(adapter.stability) or mark)
            for adapter in api.adapters
        ]
    return lines


def _mark(stability: StabilityDecl | None) -> LineMark | None:
    if stability is None:
        return None
    if stability.stability == "experimental":
        return "experimental"
    return DeprecatedMark(stability.removed_in, stability.replacement)
