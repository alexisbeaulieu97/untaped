"""Typed exceptions for the ``shelf`` plugin."""

from __future__ import annotations

from untaped.sdk import System, UntapedError


class ShelfError(UntapedError):
    """Base for shelf plugin failures: shelf has no service, so its files are to blame."""

    system = System.LOCAL
