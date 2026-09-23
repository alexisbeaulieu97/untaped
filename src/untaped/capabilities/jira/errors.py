"""Typed exceptions for the Jira capability.

Every Jira failure subclasses :class:`JiraError`. The mapping from HTTP
statuses to these types lives in ``infrastructure.errors``, so the
application layer never sees a raw ``HttpStatusError``.
"""

from __future__ import annotations

from untaped.capability_api import UntapedError


class JiraError(UntapedError):
    """Base for Jira capability failures."""


class JiraApiError(JiraError):
    """Jira answered with an error status (other than a rejected token)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class JiraTransitionError(JiraError):
    """A transition name matches no, or more than one, available transition."""


__all__ = ["JiraApiError", "JiraError", "JiraTransitionError"]
