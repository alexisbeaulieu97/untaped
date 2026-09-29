"""Typed exceptions for the Jira capability.

Every Jira failure subclasses :class:`JiraError`. The mapping from HTTP
statuses to these types lives in ``infrastructure.errors``, so the
application layer never sees a raw ``HttpStatusError``. Their ``system`` is
``jira``; a :class:`JiraApiError` keeps the category of the HTTP failure it
replaces (403 ``permission``, 404 ``not_found``, 5xx ``unavailable``, …).
"""

from __future__ import annotations

from collections.abc import Mapping

from untaped.capability_api import ErrorCategory, UntapedError


class JiraError(UntapedError):
    """Base for Jira capability failures."""

    system = "jira"


class JiraApiError(JiraError):
    """Jira answered with an error status (other than a rejected token)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        category: ErrorCategory | str | None = None,
        system: str | None = None,
        hint: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message, category=category, system=system, hint=hint, details=details)
        self.status_code = status_code
        self.url = url


class JiraTransitionError(JiraError):
    """A transition name matches no (``not_found``), or more than one, available transition."""

    category = ErrorCategory.INVALID


__all__ = ["JiraApiError", "JiraError", "JiraTransitionError"]
