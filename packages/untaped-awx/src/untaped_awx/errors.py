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
