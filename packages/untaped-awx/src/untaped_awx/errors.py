"""Typed exceptions for the AWX bounded context.

Concrete mapping from HTTP status to exception type lives in
``infrastructure.errors`` (it consumes the response body for actionable
messages). These types are surfaced to the CLI via
:func:`untaped_core.report_errors`.
"""

from __future__ import annotations

from typing import Any

from untaped_core import UntapedError


class AwxApiError(UntapedError):
    """Raised when the AWX API returns an error or behaves unexpectedly."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.url = url


class BadRequest(AwxApiError):
    """4xx response indicating malformed input (typically 400)."""


class PermissionDenied(AwxApiError):
    """403 — token authenticated but lacks the necessary permission."""


class ResourceNotFound(AwxApiError):
    """404 — looked-up resource does not exist."""

    def __init__(
        self,
        kind: str,
        identity: dict[str, Any],
        *,
        status: int | None = 404,
        body: str | None = None,
        url: str | None = None,
    ) -> None:
        identity_str = ", ".join(f"{k}={v!r}" for k, v in identity.items())
        super().__init__(
            f"{kind} not found ({identity_str})",
            status=status,
            body=body,
            url=url,
        )
        self.kind = kind
        self.identity = identity


class Conflict(AwxApiError):
    """409 — resource state conflicts with the request (e.g. concurrent edit)."""


class AmbiguousIdentityError(AwxApiError):
    """Raised when an identity-by-name lookup matches more than one record.

    AWX scopes some names by organization or another parent. A query that
    drops the scope (or uses the wrong one) can match several records;
    silently picking the first one would target whichever the server
    happened to order ahead. Surface ambiguity instead so the caller can
    add the missing scope.
    """

    def __init__(
        self,
        kind: str,
        identity: dict[str, Any],
        *,
        match_count: int | None = None,
    ) -> None:
        # AWX's URL-filter convention (`<key>__name`) leaks into the params
        # dicts callers naturally reach for; strip the suffix here so the
        # user-visible message reads as a plain identity.
        cleaned: dict[str, Any] = {
            (k.removesuffix("__name") if isinstance(k, str) else k): v for k, v in identity.items()
        }
        identity_str = ", ".join(f"{k}={v!r}" for k, v in cleaned.items())
        suffix = f" (matched {match_count} records)" if match_count is not None else ""
        super().__init__(
            f"ambiguous {kind} identity ({identity_str}){suffix}; "
            "narrow the lookup with the missing scope."
        )
        self.kind = kind
        self.identity = cleaned
        self.match_count = match_count
