"""Typed exceptions for the ``shelf`` plugin."""

from __future__ import annotations

from untaped.sdk import UntapedError


class ShelfError(UntapedError):
    """Base for shelf plugin failures."""

    system = "shelf"
