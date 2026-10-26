"""Typed exceptions for the Jira plugin, and the mapping from Jira's HTTP failures.

Every Jira failure subclasses :class:`JiraError`, whose ``system`` is the
plugin. :func:`map_jira_errors` turns a failed call into one, so commands
never see a raw ``HttpStatusError``:

- 401 becomes an ``auth`` :class:`ConfigError` with the token's hint (exit ``4``);
- 404 on a named resource becomes ``<noun> not found: 'KEY'``;
- every other status becomes a :class:`JiraApiError` carrying Jira's own
  ``errorMessages`` / ``errors`` text when the body has them, and the HTTP
  failure's category (403 ``permission``, 404 ``not_found``, 5xx
  ``unavailable``, …).

Transport failures (no response at all) pass through unchanged (``unavailable``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from untaped.sdk import (
    ErrorCategory,
    HttpStatusError,
    Service,
    UntapedError,
    attribution,
    not_found,
    rejected_token_error,
)
from untaped_jira.settings import JiraSettings


class JiraError(UntapedError):
    """Base for Jira plugin failures."""

    system = Service(JiraSettings)


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


def to_jira_error(
    err: HttpStatusError, *, noun: str | None = None, name: str | None = None
) -> UntapedError:
    """The typed error for one failed Jira call (``noun``/``name`` name its target)."""
    status = err.status_code
    if status == 401:
        return rejected_token_error(
            Service(JiraSettings).name(), "Jira rejected the token (HTTP 401)", cause=err
        )
    detail = _jira_detail(err.body)
    if status == 404 and noun is not None and name is not None:
        message = not_found(noun, name)
    elif status == 403:
        message = f"permission denied (HTTP 403){f': {detail}' if detail else ''}"
    else:
        message = f"{err}{f': {detail}' if detail else ''}"
    return JiraApiError(message, status_code=status, url=err.url, **attribution(err))


@contextmanager
def map_jira_errors(*, noun: str | None = None, name: str | None = None) -> Iterator[None]:
    """Re-raise any :class:`HttpStatusError` inside the block as a Jira error."""
    try:
        yield
    except HttpStatusError as exc:
        raise to_jira_error(exc, noun=noun, name=name) from exc


def _jira_detail(body: str | None) -> str | None:
    """Jira's own error text: ``errorMessages`` then ``field: message`` pairs."""
    if not body:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    parts: list[str] = []
    messages = data.get("errorMessages")
    if isinstance(messages, list):
        parts.extend(str(message) for message in messages if message)
    errors = data.get("errors")
    if isinstance(errors, dict):
        parts.extend(f"{field}: {message}" for field, message in errors.items() if message)
    return "; ".join(parts) or None


__all__ = ["JiraApiError", "JiraError", "JiraTransitionError", "map_jira_errors", "to_jira_error"]
