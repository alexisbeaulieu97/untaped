"""Ansible capability exception hierarchy.

Every error the capability raises on purpose derives from :class:`AnsibleError`
(itself an :class:`~untaped.sdk.UntapedError`) so ``report_errors`` turns it
into a clean ``error: ...`` message instead of a traceback. Failures are
attributed to ``local`` (sources, the dependency index, files), except
:class:`GitCacheError` (``git``, ``unavailable`` when git timed out or lost
the network).
"""

from __future__ import annotations

from untaped.sdk import UntapedError


class AnsibleError(UntapedError):
    """Base for Ansible capability errors."""

    system = "local"


class GitCacheError(AnsibleError):
    """Raised when local Git cache operations fail."""

    system = "git"


class DependencyIndexError(AnsibleError):
    """Raised when the SQLite dependency index cannot be opened or queried."""
