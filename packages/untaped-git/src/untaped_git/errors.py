"""Git plugin errors: failures reaching remotes or keeping repos, attributed to ``git``."""

from __future__ import annotations

from untaped.sdk import UntapedError


class GitError(UntapedError):
    """Base for git plugin errors (remotes, credentials, the repo store)."""

    system = "git"


class StoreError(GitError):
    """A repo store operation failed (its category says whether a retry can help)."""
