"""Workspace-specific exception hierarchy."""

from __future__ import annotations

from untaped_core.errors import UntapedError


class WorkspaceError(UntapedError):
    """Base for workspace-domain errors."""


class GitError(WorkspaceError):
    """Raised when an underlying ``git`` command exits non-zero."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class ManifestError(WorkspaceError):
    """Raised when ``untaped.yml`` is missing or invalid."""


class RegistryError(WorkspaceError):
    """Raised for registry mismatches (unknown name, duplicate path, …)."""
