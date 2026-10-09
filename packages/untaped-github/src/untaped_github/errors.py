"""GitHub plugin exception hierarchy.

Every error the plugin raises on purpose derives from :class:`GithubError`
(itself an :class:`~untaped.sdk.UntapedError`) so ``report_errors``
turns it into a clean ``error: ...`` line instead of a traceback. Failures are
attributed to ``github``, except :class:`GitCorpusError` (``git``) and
repository-inventory disk failures (``local``); a
:class:`GithubGraphqlError` takes its category from its ``kind`` (a rate
limit is ``unavailable``, bad credentials ``auth``, a forbidden scope
``permission``).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from untaped.sdk import ErrorCategory, UntapedError

GithubGraphqlErrorKind = Literal[
    "rate_limited",
    "secondary_rate_limited",
    "auth",
    "forbidden",
    "unknown",
]

_KIND_CATEGORIES: Mapping[GithubGraphqlErrorKind, ErrorCategory] = MappingProxyType(
    {
        "rate_limited": ErrorCategory.UNAVAILABLE,
        "secondary_rate_limited": ErrorCategory.UNAVAILABLE,
        "auth": ErrorCategory.AUTH,
        "forbidden": ErrorCategory.PERMISSION,
    }
)


class GithubError(UntapedError):
    """Base for GitHub plugin errors."""

    system = "github"


class GithubGraphqlError(GithubError):
    """Global GitHub GraphQL failure that should abort batched operations.

    Its category follows ``kind`` unless one is given (``unknown`` stays
    ``failed``).
    """

    def __init__(
        self,
        message: str,
        *,
        kind: GithubGraphqlErrorKind,
        status_code: int | None = None,
        url: str | None = None,
        body: str | None = None,
        category: ErrorCategory | str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message, category=category or _KIND_CATEGORIES.get(kind), details=details)
        self.kind = kind
        self.status_code = status_code
        self.url = url
        self.body = body


class GitCorpusError(GithubError):
    """Local Git corpus operation failure."""

    system = "git"


__all__ = ["GitCorpusError", "GithubError", "GithubGraphqlError", "GithubGraphqlErrorKind"]
