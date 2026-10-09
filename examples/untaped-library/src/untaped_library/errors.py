"""Typed exceptions for the ``library`` plugin."""

from __future__ import annotations

from untaped.sdk import UntapedError


class LibraryError(UntapedError):
    """Base for library plugin failures."""

    system = "library"
