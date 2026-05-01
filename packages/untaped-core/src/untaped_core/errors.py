"""Base exception hierarchy for untaped."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import ValidationError


class UntapedError(Exception):
    """Root of the untaped exception hierarchy."""


class ConfigError(UntapedError):
    """Raised when configuration is missing, malformed, or invalid."""


class HttpError(UntapedError):
    """Raised when an HTTP call fails (network, timeout, or non-2xx status)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


def first_validation_error(exc: ValidationError) -> str:
    """Format the first issue from a Pydantic ``ValidationError`` as ``loc: msg``."""
    errs = exc.errors()
    if not errs:
        return str(exc)
    err = errs[0]
    loc = ".".join(str(part) for part in err.get("loc", ()))
    msg = err.get("msg", "invalid value")
    return f"{loc}: {msg}" if loc else msg
