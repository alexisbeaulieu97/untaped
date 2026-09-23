"""Domain errors for GitHub API access."""

from __future__ import annotations

from typing import Literal

from untaped.capability_api import HttpError, UntapedError

GithubGraphqlErrorKind = Literal[
    "rate_limited",
    "secondary_rate_limited",
    "auth",
    "forbidden",
    "unknown",
]


class GithubGraphqlError(UntapedError):
    """Global GitHub GraphQL failure that should abort batched operations."""

    def __init__(
        self,
        message: str,
        *,
        kind: GithubGraphqlErrorKind,
        status_code: int | None = None,
        url: str | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.url = url
        self.body = body


class GitCorpusError(UntapedError):
    """Local Git corpus operation failure."""


_GLOBAL_GRAPHQL_KINDS = frozenset({"rate_limited", "secondary_rate_limited", "auth"})
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


def is_global_github_failure(exc: BaseException) -> bool:
    """Return whether ``exc`` (or a wrapped cause) fails every GitHub request alike.

    Bad credentials (401) and rate limits hit every repository the same way, so
    callers isolating per-repo failures must re-raise these instead of turning
    each repository into an individual failure row.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, GithubGraphqlError) and current.kind in _GLOBAL_GRAPHQL_KINDS:
            return True
        if isinstance(current, HttpError) and (
            current.status_code == 401 or is_rate_limited(current.status_code, current.body)
        ):
            return True
        current = current.__cause__
    return False
