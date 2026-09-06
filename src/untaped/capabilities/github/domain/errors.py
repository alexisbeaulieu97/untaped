"""Domain errors for GitHub API access."""

from __future__ import annotations

from typing import Literal

from untaped.api import UntapedError

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
