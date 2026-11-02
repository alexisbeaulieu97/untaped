"""How the ref probe reads GraphQL failures.

:func:`classify_errors` separates the repositories GitHub reports as
``NOT_FOUND`` or ``FORBIDDEN`` (collected, not raised) and raises any other
error as a :class:`untaped_github.GithubGraphqlError`;
:func:`graphql_http_error` turns an HTTP failure into one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from untaped.sdk import ErrorCategory, HttpError
from untaped_github.errors import GithubGraphqlError, GithubGraphqlErrorKind

_MISSING_ERROR_TYPES = frozenset({"NOT_FOUND", "FORBIDDEN"})


def classify_errors(payload: dict[str, Any]) -> set[str]:
    """Return aliases of missing repos; raise on any other GraphQL error."""
    missing: set[str] = set()
    for error in payload.get("errors") or ():
        if not isinstance(error, dict):
            raise _graphql_payload_error(error)
        alias = _repo_alias_from_path(error.get("path"))
        if error.get("type") in _MISSING_ERROR_TYPES and alias is not None:
            missing.add(alias)
            continue
        raise _graphql_payload_error(error)
    return missing


def _repo_alias_from_path(path: object) -> str | None:
    if not isinstance(path, list | tuple) or len(path) != 1:
        return None
    alias = path[0]
    if isinstance(alias, str) and alias.startswith("r") and alias[1:].isdigit():
        return alias
    return None


def graphql_http_error(exc: HttpError) -> GithubGraphqlError:
    detail = _message_from_body(exc.body)
    kind = _classify_graphql_failure(status_code=exc.status_code, message=detail)
    return _github_graphql_error(
        kind,
        detail,
        status_code=exc.status_code,
        url=exc.url,
        body=exc.body,
        category=exc.category if kind == "unknown" else None,
        details=exc.details,
    )


def _graphql_payload_error(error: object) -> GithubGraphqlError:
    if isinstance(error, dict):
        detail = _message_from_graphql_error(error)
        error_type = error.get("type")
        kind = _classify_graphql_failure(
            status_code=None,
            message=detail,
            error_type=str(error_type) if error_type is not None else None,
        )
    else:
        detail = repr(error)
        kind = "unknown"
    return _github_graphql_error(kind, detail)


def _message_from_graphql_error(error: dict[str, Any]) -> str | None:
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        return message
    return repr(error)


def _message_from_body(body: str | None) -> str | None:
    if body is None:
        return None
    stripped = body.strip()
    if not stripped:
        return None
    try:
        decoded = json.loads(stripped)
    except ValueError:
        return stripped
    if isinstance(decoded, dict):
        for key in ("message", "error", "detail"):
            message = decoded.get(key)
            if isinstance(message, str) and message.strip():
                return message.strip()
        errors = decoded.get("errors")
        if isinstance(errors, list):
            for item in errors:
                if isinstance(item, dict):
                    message = item.get("message")
                    if isinstance(message, str) and message.strip():
                        return message.strip()
    return stripped


def _classify_graphql_failure(
    *,
    status_code: int | None,
    message: str | None,
    error_type: str | None = None,
) -> GithubGraphqlErrorKind:
    haystack = f"{message or ''} {error_type or ''}".lower()
    if status_code == 401 or "bad credentials" in haystack or "requires authentication" in haystack:
        return "auth"
    if "secondary rate limit" in haystack or "abuse detection" in haystack:
        return "secondary_rate_limited"
    if (
        error_type == "RATE_LIMITED"
        or status_code == 429
        or "rate limit exceeded" in haystack
        or "api rate limit" in haystack
    ):
        return "rate_limited"
    if status_code == 403 or error_type == "FORBIDDEN" or "resource not accessible" in haystack:
        return "forbidden"
    return "unknown"


def _github_graphql_error(
    kind: GithubGraphqlErrorKind,
    detail: str | None,
    *,
    status_code: int | None = None,
    url: str | None = None,
    body: str | None = None,
    category: ErrorCategory | None = None,
    details: Mapping[str, object] | None = None,
) -> GithubGraphqlError:
    prefixes = {
        "rate_limited": "github graphql rate limit exceeded",
        "secondary_rate_limited": "github graphql secondary rate limit exceeded",
        "auth": "github graphql authentication failed",
        "forbidden": "github graphql access forbidden",
        "unknown": "github graphql error",
    }
    fallback = f"HTTP {status_code}" if status_code is not None else "unknown failure"
    if url and status_code is not None:
        fallback = f"{fallback} for {url}"
    message = f"{prefixes[kind]}: {detail or fallback}"
    return GithubGraphqlError(
        message,
        kind=kind,
        status_code=status_code,
        url=url,
        body=body,
        category=category,
        details=details,
    )
