"""The "Servers" section of a plugin's page: how core finds a server's version, and the adapters.

Rendered from the declaration (a server API block's :class:`ServerApiDecl`
and its cache part's setting), so the page imports no plugin. One paragraph
per server API: when core detects the version, the setting that tunes how
long a detection is trusted, the override, then ``detect``'s docstring and
one table row per adapter in the order core tries them.
"""

from __future__ import annotations

from untaped.core.docs._docstring import cell, markdown
from untaped.runtime.declaration import BlockDecl, PluginDecl, StabilityDecl
from untaped.sdk.marks import removal_text
from untaped.sdk.storage.cache import _max_age_setting
from untaped.sdk.words import duration_text


def section(plugin: PluginDecl, distribution: str | None) -> list[str]:
    """``## Servers`` with each of ``plugin``'s server APIs; ``[]`` when it holds none."""
    apis = [block for block in plugin.blocks if block.server_api is not None]
    lines: list[str] = []
    for block in apis:
        if len(apis) > 1:
            lines += ["", f"### `{block.name}`"]
        lines += _api(plugin, block, distribution)
    return ["", "## Servers", *lines] if lines else []


def _api(plugin: PluginDecl, block: BlockDecl, distribution: str | None) -> list[str]:
    api = block.server_api
    assert api is not None  # section: server API blocks only
    name = plugin.name
    cache = next(
        (each for each in plugin.blocks if each.attribute == block.attribute and each.cache),
        None,
    )
    seconds = None if cache is None or cache.cache is None else cache.cache.max_age_seconds
    when = "on first use"
    if cache is not None and seconds is not None:
        setting = _max_age_setting(name, cache.name)
        when += f" and checks again after {duration_text(seconds)} (`{setting}`)"
    lines = [
        "",
        f"{distribution or name} detects which version its server runs {when}; "
        f"`{name}.{api.override}`, set beside `{name}.{api.url}`, says it instead.",
    ]
    if api.detect:
        lines += ["", markdown(api.detect)]
    lines += ["", "| Server | How | Recorded against | |", "|---|---|---|---|"]
    for adapter in api.adapters:
        cells = (adapter.summary, adapter.doc, ", ".join(adapter.recorded))
        mark = _mark(adapter.stability, distribution)
        lines.append("| " + " | ".join(cell(text) for text in (*cells, mark)) + " |")
    return lines


def _mark(stability: StabilityDecl | None, distribution: str | None) -> str:
    """An adapter's own mark as its row's last cell: when a deprecated one goes."""
    if stability is None:
        return ""
    if stability.stability == "experimental":
        return "experimental"
    removal = removal_text(stability.removed_in, distribution)
    return "deprecated" + ("" if removal is None else f", removed in {removal}")
