"""Dotfiles errors: local failures by default, git failures attributed to ``git``."""

from __future__ import annotations

from untaped.sdk import ErrorCategory, System, UntapedError


class DotfilesError(UntapedError):
    """Base for dotfiles errors (local filesystem and state by default)."""

    system = System.LOCAL


class PackageError(DotfilesError):
    """A package file does not parse or breaks a package rule."""

    category = ErrorCategory.INVALID


class GitError(DotfilesError):
    """A git operation on a source's clone failed."""

    system = System.GIT


class SourceNotFoundError(DotfilesError):
    """No subscribed source has that name."""

    category = ErrorCategory.NOT_FOUND
