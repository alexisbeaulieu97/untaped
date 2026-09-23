"""Ansible capability exception hierarchy.

Every error the capability raises on purpose derives from :class:`AnsibleError`
(itself an :class:`~untaped.capability_api.UntapedError`) so ``report_errors`` turns it
into a clean ``error: ...`` message instead of a traceback.
"""

from __future__ import annotations

from untaped.capability_api import UntapedError


class AnsibleError(UntapedError):
    """Base for Ansible capability errors."""


class GitCacheError(AnsibleError):
    """Raised when local Git cache operations fail."""


class DependencyIndexError(AnsibleError):
    """Raised when the SQLite dependency index cannot be opened or queried."""
