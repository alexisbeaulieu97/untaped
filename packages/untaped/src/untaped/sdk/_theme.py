"""The theme model: the presentation tokens every view renders with, free of rendering.

A run's rendering names the theme its text lines render with
(``untaped.sdk._run.Rendering``), so the model lives in the SDK; the
built-in presets and the ``ui`` settings that pick one are the runtime's
(``untaped.runtime.theme``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BorderStyle = Literal["rounded", "square", "ascii", "none"]
CollectionView = Literal["table", "list"]
DetailView = Literal["list", "table"]
Density = Literal["normal", "compact"]
#: Status symbols that prefix ``success``/``warning``/``error``/``info`` lines
#: (empty by default).
STATUS_SYMBOLS: dict[str, str] = {
    "success": "",
    "warning": "",
    "error": "",
    "info": "",
}

#: Glyphs screens draw with; every default is one cell wide.
SCREEN_SYMBOLS: dict[str, str] = {
    "chosen": "\u25b6",
    "checked": "\u2713",
    "unchecked": " ",
    "on": "\u2713",
    "off": "\u2717",
    "expand": "\u25bc",
    "collapse": "\u25b2",
    "mask": "\u2022",
    "ellipsis": "\u2026",
    "separator": "\u00b7",
    "cycle.left": "\u2039",
    "cycle.right": "\u203a",
    "tab.active": "\u2501",
    "tab.inactive": "\u2500",
    "tree.open": "\u25be",
    "tree.closed": "\u25b8",
    "tag.add": "+",
    "tag.remove": "\u2715",
    "heading": "\u25c6",
    "dash": "\u2014",
}

#: Glyphs a checklist (``untaped doctor``) marks its rows and fixes with; one cell wide.
CHECK_SYMBOLS: dict[str, str] = {
    "check.pass": "\u2713",
    "check.warn": "\u25b2",
    "check.partial": "\u25d0",
    "check.fail": "\u2717",
    "check.skip": "\u25cb",
    "check.fix": "\u2192",
}

DEFAULT_SYMBOLS: dict[str, str] = {**STATUS_SYMBOLS, **SCREEN_SYMBOLS, **CHECK_SYMBOLS}


class ThemeSpec(BaseModel):
    """Terminal presentation tokens and default semantic view choices."""

    border: BorderStyle = "rounded"
    density: Density = "normal"
    collection_view: CollectionView = "table"
    detail_view: DetailView = "list"
    hide_empty_columns: bool = True
    symbols: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_SYMBOLS))
    color_roles: dict[str, str] = Field(default_factory=dict)
