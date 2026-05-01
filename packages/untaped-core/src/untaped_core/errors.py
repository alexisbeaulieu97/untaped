"""Base exception hierarchy for untaped."""

from __future__ import annotations


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
