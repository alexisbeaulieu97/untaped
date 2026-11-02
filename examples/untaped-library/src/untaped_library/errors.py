"""Typed exceptions for the ``library`` plugin."""

from __future__ import annotations

from untaped.sdk import System, UntapedError


class LibraryError(UntapedError):
    """Base for library plugin failures: library has no service, so its files are to blame."""

    system = System.LOCAL
