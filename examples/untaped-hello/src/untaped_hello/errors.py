"""Typed exceptions for the ``hello`` plugin."""

from __future__ import annotations

from untaped.sdk import System, UntapedError


class HelloError(UntapedError):
    """Base for hello plugin failures: hello has no service, so its files are to blame."""

    system = System.LOCAL
