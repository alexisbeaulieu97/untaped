"""Typed exceptions for the ``hello`` plugin."""

from __future__ import annotations

from untaped.sdk import UntapedError


class HelloError(UntapedError):
    """Base for hello plugin failures."""

    system = "hello"
