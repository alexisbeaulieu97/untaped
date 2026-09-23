"""GitHub capability exception hierarchy.

Every error the capability raises on purpose derives from :class:`GithubError`
(itself an :class:`~untaped.capability_api.UntapedError`) so ``report_errors``
turns it into a clean ``error: ...`` line instead of a traceback.
"""

from __future__ import annotations

from typing import Literal

from untaped.capability_api import UntapedError

GithubGraphqlErrorKind = Literal[
    "rate_limited",
    "secondary_rate_limited",
    "auth",
    "forbidden",
    "unknown",
]


class GithubError(UntapedError):
    """Base for GitHub capability errors."""


class GithubGraphqlError(GithubError):
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


class GitCorpusError(GithubError):
    """Local Git corpus operation failure."""


__all__ = ["GitCorpusError", "GithubError", "GithubGraphqlError", "GithubGraphqlErrorKind"]
