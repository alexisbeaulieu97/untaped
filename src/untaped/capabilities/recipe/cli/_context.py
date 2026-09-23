"""The UI context recipe commands share for messages, progress, and confirms."""

from __future__ import annotations

from untaped.api import UiContext, ui_context


def recipe_ui() -> UiContext:
    """Return the non-strict UI context (never fails when no terminal is attached)."""
    return ui_context(strict=False)
