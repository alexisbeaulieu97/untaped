"""Ansible plugin exception hierarchy.

Every error the plugin raises on purpose derives from :class:`AnsibleError`
(itself an :class:`~untaped.sdk.UntapedError`) so core's error boundary turns it
into a clean ``error: ...`` message instead of a traceback. Failures are
attributed to ``local`` (sources, the dependency index, files), except
:class:`RepoStoreError` (``git``, ``unavailable`` when git timed out or lost
the network).
"""

from __future__ import annotations

from untaped.sdk import System, UntapedError


class AnsibleError(UntapedError):
    """Base for Ansible plugin errors."""

    system = System.LOCAL


class RepoStoreError(AnsibleError):
    """Raised when a source's repo store operation (fetch, ``ls-remote``, blob read) fails."""

    system = System.GIT


class DependencyIndexError(AnsibleError):
    """Raised when the SQLite dependency index cannot be opened or queried."""
