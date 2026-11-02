"""Workspace errors: local failures by default, git failures attributed to ``git``."""

from __future__ import annotations

from untaped.sdk import ErrorCategory, System, UntapedError


class WorkspaceError(UntapedError):
    """Base for workspace errors (local filesystem and state by default)."""

    system = System.LOCAL


class GitError(WorkspaceError):
    """A git operation on a store repo or worktree failed."""

    system = System.GIT


class WorkspaceNotFoundError(WorkspaceError):
    """No active workspace has that name (or contains the current directory)."""

    category = ErrorCategory.NOT_FOUND
