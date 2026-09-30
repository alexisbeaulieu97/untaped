"""Workspace-specific exception hierarchy.

Failures are attributed to ``local`` (the workspace's files and registry)
unless a class says otherwise: :class:`GitError` is ``git`` (``unavailable``
when git timed out or lost the network), an unreadable ``untaped.yml`` is
``invalid``, an unknown workspace or ``--repo`` identifier is ``not_found``.
"""

from __future__ import annotations

from typing import Any

from untaped.capability_api import ErrorCategory, UntapedError, plural


class WorkspaceError(UntapedError):
    """Base for workspace-domain errors."""

    system = "local"


class GitError(WorkspaceError):
    """Raised when an underlying ``git`` command fails.

    Covers three failure modes: non-zero exit (``returncode`` set), timeout
    (``returncode=None``, message includes ``"timed out after Ns"``), and
    "git binary not on PATH" (``returncode=None``, message names the
    missing binary). Callers that want to differentiate read ``category``:
    a timeout or a transient transport failure is ``unavailable``, a
    missing binary is ``config`` in ``local`` (the git adapter passes on
    the attribution of the core ``GitCommandError``).
    """

    system = "git"

    def __init__(self, message: str, *, returncode: int | None = None, **attributed: Any) -> None:
        super().__init__(message, **attributed)
        self.returncode = returncode


class PartialRemovalError(WorkspaceError):
    """A repo left the manifest but its clone could not be deleted (``--prune``).

    Keeps the deletion failure's attribution; the row it becomes is ``partial``.
    """


class ManifestError(WorkspaceError):
    """Raised when ``untaped.yml`` is missing (``not_found``) or invalid (``invalid``).

    The manifest is the workspace's own data file, not untaped's settings,
    so an invalid one is an invalid input (exit ``1``), not a setup error.
    """

    category = ErrorCategory.INVALID


class RegistryError(WorkspaceError):
    """Raised for registry mismatches (unknown name ``not_found``, duplicate ``conflict``, …)."""

    category = ErrorCategory.NOT_FOUND


class UnmatchedRepoFilterError(WorkspaceError):
    """Raised when a repo selector contains identifiers no repo matches (``not_found``).

    Carries the unmatched identifiers so callers can react precisely
    (e.g. format a ``BadParameter`` message, or aggregate across
    multiple invocations under ``--all``).
    """

    category = ErrorCategory.NOT_FOUND

    def __init__(self, unmatched: tuple[str, ...]) -> None:
        noun = plural(len(unmatched), "unknown repo identifier")
        super().__init__(f"{noun} for --repo: {', '.join(unmatched)}")
        self.unmatched = unmatched
