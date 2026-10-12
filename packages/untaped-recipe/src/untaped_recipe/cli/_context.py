"""The UI context recipe commands share for prompts, stdin, and confirms."""

from __future__ import annotations

from untaped.sdk import UiContext, ui_context


def recipe_ui() -> UiContext:
    """Return the non-strict UI context (never fails when no terminal is attached)."""
    return ui_context(strict=False)
