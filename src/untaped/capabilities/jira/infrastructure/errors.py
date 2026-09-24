"""Map HTTP status failures from the Jira REST API to typed Jira exceptions.

- 401 becomes a :class:`ConfigError` with a ``jira.token`` hint;
- 404 on a named resource becomes ``<noun> not found: 'KEY'``;
- every other status becomes a :class:`JiraApiError` carrying Jira's own
  ``errorMessages`` / ``errors`` text when the body has them.

Transport failures (no response at all) pass through unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

from untaped.capabilities.jira.errors import JiraApiError
from untaped.capability_api import ConfigError, HttpStatusError, UntapedError, hint, not_found


def to_jira_error(
    err: HttpStatusError, *, noun: str | None = None, name: str | None = None
) -> UntapedError:
    """The typed error for one failed Jira call (``noun``/``name`` name its target)."""
    status = err.status_code
    if status == 401:
        return ConfigError(
            f"Jira rejected the token (HTTP 401)\n{hint('config set jira.token --prompt')}"
        )
    detail = _jira_detail(err.body)
    if status == 404 and noun is not None and name is not None:
        message = not_found(noun, name)
    elif status == 403:
        message = f"permission denied (HTTP 403){f': {detail}' if detail else ''}"
    else:
        message = f"{err}{f': {detail}' if detail else ''}"
    return JiraApiError(message, status_code=status, url=err.url)


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


__all__ = ["map_jira_errors", "to_jira_error"]
