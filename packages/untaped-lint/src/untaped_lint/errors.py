"""Lint plugin exception hierarchy.

Every error the plugin raises on purpose derives from :class:`LintError`, so
core's error boundary prints it as a clean ``error: ...`` line.
"""

from __future__ import annotations

from pathlib import Path

from untaped.sdk import ErrorCategory, UntapedError


class LintError(UntapedError):
    """Base for lint plugin errors: invalid local input unless a subclass says otherwise."""

    category = ErrorCategory.INVALID
    system = "lint"


class UnreadableSourceError(LintError):
    """A Python file the file rules can't read: not UTF-8, or not Python."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason
