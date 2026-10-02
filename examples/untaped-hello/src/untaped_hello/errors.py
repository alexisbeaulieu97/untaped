"""Typed exceptions for the ``hello`` capability."""

from __future__ import annotations

from untaped.sdk import UntapedError


class HelloError(UntapedError):
    """Base for hello capability failures."""

    system = "hello"
