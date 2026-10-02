"""Terminal presentation of ``ansible graph``'s tree: glyphs and styles.

The domain builds the tree as role-tagged segments; this module picks the
glyph set from the theme's border (ASCII for ``ascii``) and styles each role
from the theme's color roles, with readable defaults.
"""

from __future__ import annotations

from rich.text import Text

from untaped.sdk import UiContext
from untaped_ansible.domain.renderers import (
    ASCII_GLYPHS,
    UNICODE_GLYPHS,
    TreeGlyphs,
    TreeLine,
    TreeRole,
)

_ROLE_STYLES: dict[TreeRole, tuple[str | None, str | None]] = {
    "target": (None, "bold"),
    "section": ("header", "bold"),
    "guide": ("border", "dim"),
    "node": ("value", None),
    "unresolved": ("error", "red"),
    "note": (None, "dim"),
    "ref": ("key", "cyan"),
    "cycle": ("warning", "yellow"),
}
"""Per role: the theme color role that styles it, and the style used when the theme sets none."""


def tree_glyphs(ui: UiContext) -> TreeGlyphs:
    return ASCII_GLYPHS if ui.theme.border == "ascii" else UNICODE_GLYPHS


def print_tree(lines: list[TreeLine], ui: UiContext) -> None:
    """Print tree lines to stdout in one write, styled where the stream supports color.

    Lines never wrap: one too wide for the terminal ends in an ellipsis.
    """
    styles = {
        role: (ui.theme.color_roles.get(theme_role) if theme_role else None) or default or ""
        for role, (theme_role, default) in _ROLE_STYLES.items()
    }
    text = Text("\n").join(
        Text.assemble(*((segment.text, styles[segment.role]) for segment in line)) for line in lines
    )
    ui.styled(text, truncate=True)
