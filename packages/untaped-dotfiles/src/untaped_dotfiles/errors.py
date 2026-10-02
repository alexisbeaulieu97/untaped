"""Dotfiles errors: local failures by default, git failures attributed to ``git``."""

from __future__ import annotations

from untaped.sdk import ErrorCategory, UntapedError


class DotfilesError(UntapedError):
    """Base for dotfiles errors (local filesystem and state by default)."""

    system = "local"


class ManifestError(DotfilesError):
    """A manifest does not parse or breaks a manifest rule."""

    category = ErrorCategory.INVALID


class GitError(DotfilesError):
    """A git operation on a subscribed repo failed."""

    system = "git"


class RepoNotFoundError(DotfilesError):
    """No subscribed repo has that name."""

    category = ErrorCategory.NOT_FOUND


class ItemNotFoundError(DotfilesError):
    """No manifest item has that name."""

    category = ErrorCategory.NOT_FOUND
