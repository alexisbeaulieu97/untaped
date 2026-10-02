"""GitHub failure classification.

The error classes live in :mod:`untaped_github.errors`; they are
re-exported here for existing importers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from untaped.sdk import ErrorCategory, HttpError, UntapedError, rejected_token_error
from untaped_github.errors import (
    GitCorpusError,
    GithubError,
    GithubGraphqlError,
    GithubGraphqlErrorKind,
)

__all__ = [
    "GitCorpusError",
    "GithubError",
    "GithubGraphqlError",
    "GithubGraphqlErrorKind",
    "github_failures",
    "is_auth_failure",
    "is_global_github_failure",
    "is_rate_limit_failure",
    "is_rate_limited",
]

_RATE_LIMIT_KINDS = frozenset({"rate_limited", "secondary_rate_limited"})
_GLOBAL_GRAPHQL_KINDS = _RATE_LIMIT_KINDS | {"auth"}
_RATE_LIMIT_MARKERS = ("rate limit", "abuse detection", "x-ratelimit-remaining: 0")


def is_rate_limited(status_code: int | None, body: str | None) -> bool:
    """Return whether an HTTP failure is a GitHub primary or secondary rate limit.

    GitHub answers an exhausted budget with 429, or with 403 plus a
    "rate limit" message; a plain 403 is a per-resource permission failure.
    """
    if status_code == 429:
        return True
    return status_code == 403 and any(
        marker in (body or "").lower() for marker in _RATE_LIMIT_MARKERS
    )


def _chain(exc: BaseException) -> list[BaseException]:
    seen: set[int] = set()
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__
    return chain


def is_rate_limit_failure(exc: BaseException) -> bool:
    """Return whether ``exc`` (or a wrapped cause) is a GitHub primary or secondary rate limit."""
    return any(
        (isinstance(current, GithubGraphqlError) and current.kind in _RATE_LIMIT_KINDS)
        or (isinstance(current, HttpError) and is_rate_limited(current.status_code, current.body))
        for current in _chain(exc)
    )


def is_auth_failure(exc: BaseException) -> bool:
    """Return whether ``exc`` (or a wrapped cause) is GitHub rejecting the token."""
    return any(
        (isinstance(current, GithubGraphqlError) and current.kind == "auth")
        or (isinstance(current, HttpError) and current.status_code == 401)
        for current in _chain(exc)
    )


@contextmanager
def github_failures() -> Iterator[None]:
    """Attribute GitHub failures raised inside the block.

    A rejected token (401) becomes the standard ``auth`` error with the
    ``config set github.token`` hint; a rate limit becomes ``unavailable``
    (GitHub answers an exhausted budget with 403 too: retry later).
    """
    try:
        yield
    except UntapedError as exc:
        if is_auth_failure(exc):
            raise rejected_token_error(
                "github", "GitHub rejected the configured token (HTTP 401)", cause=exc
            ) from exc
        if is_rate_limit_failure(exc):
            exc.category = ErrorCategory.UNAVAILABLE
        raise


def is_global_github_failure(exc: BaseException) -> bool:
    """Return whether ``exc`` (or a wrapped cause) fails every GitHub request alike.

    Bad credentials (401) and rate limits hit every repository the same way, so
    callers isolating per-repo failures must re-raise these instead of turning
    each repository into an individual failure row.
    """
    for current in _chain(exc):
        if isinstance(current, GithubGraphqlError) and current.kind in _GLOBAL_GRAPHQL_KINDS:
            return True
        if isinstance(current, HttpError) and (
            current.status_code == 401 or is_rate_limited(current.status_code, current.body)
        ):
            return True
    return False
