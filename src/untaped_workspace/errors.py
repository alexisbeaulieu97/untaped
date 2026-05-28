"""Workspace-specific exception hierarchy."""

from __future__ import annotations

from untaped.errors import UntapedError


class WorkspaceError(UntapedError):
    """Base for workspace-domain errors."""


class GitError(WorkspaceError):
    """Raised when an underlying ``git`` command fails.

    Covers three failure modes: non-zero exit (``returncode`` set), timeout
    (``returncode=None``, message includes ``"timed out after Ns"``), and
    "git binary not on PATH" (``returncode=None``, message names the
    missing binary). Callers that want to differentiate today must
    inspect the message — there is no ``timed_out`` flag yet.
    """

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class ManifestError(WorkspaceError):
    """Raised when ``untaped.yml`` is missing or invalid."""


class RegistryError(WorkspaceError):
    """Raised for registry mismatches (unknown name, duplicate path, …)."""


class UnmatchedOnlyFilter(WorkspaceError):
    """Raised when ``--only`` contains identifiers no repo matches.

    Carries the unmatched identifiers so callers can react precisely
    (e.g. format a ``BadParameter`` message, or aggregate across
    multiple invocations under ``--all``).
    """

    def __init__(self, unmatched: tuple[str, ...]) -> None:
        super().__init__(f"unknown repo identifier(s) for --only: {', '.join(unmatched)}")
        self.unmatched = unmatched
