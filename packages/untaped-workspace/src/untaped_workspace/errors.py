"""Workspace errors: local failures by default, git failures attributed to ``git``."""

from __future__ import annotations

from typing import Any

from untaped.sdk import ErrorCategory, UntapedError


class WorkspaceError(UntapedError):
    """Base for workspace errors (local filesystem and state by default)."""

    system = "local"


class GitError(WorkspaceError):
    """A git operation on a cache or worktree failed."""

    system = "git"

    def __init__(self, message: str, *, returncode: int | None = None, **attributed: Any) -> None:
        super().__init__(message, **attributed)
        self.returncode = returncode


class WorkspaceNotFoundError(WorkspaceError):
    """No active workspace has that name (or contains the current directory)."""

    category = ErrorCategory.NOT_FOUND
