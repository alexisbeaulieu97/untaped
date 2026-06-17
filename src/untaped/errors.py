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
    """Raised when an HTTP call fails (network, timeout, or non-2xx status).

    ``body`` carries a UTF-8 snippet of the response body (decoded with
    ``errors="replace"``, so a non-UTF-8 charset surfaces as ``\\ufffd``
    rather than a crash) when the failure was a non-2xx status. It lets
    domain layers map status + payload into typed errors without
    re-running the request. **Capped at 2048 bytes** (``_BODY_LIMIT`` in
    :mod:`untaped.http`) so a multi-MB proxy error page doesn't
    live on the exception — and through ``report_errors`` to stderr —
    long after the underlying response is collected.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.body = body


class HttpStatusError(HttpError):
    """Raised when the server responded with a non-2xx status (>=400).

    ``status_code`` is always set (alongside ``url`` and a ``body`` snippet), so
    callers can act on "the server said no" — inspect the status/body — as
    distinct from never reaching the server (:class:`HttpTransportError`). A
    successful response whose body is unusable (bad JSON/shape) is *not* this:
    it stays a plain :class:`HttpError`.
    """


class HttpTransportError(HttpError):
    """Raised when the request never produced a response.

    Connection failures, timeouts, and DNS errors map here; ``status_code`` is
    ``None`` because no status was ever received. These are typically transient
    and worth retrying, unlike :class:`HttpStatusError`.
    """


def first_validation_error(exc: ValidationError) -> str:
    """Format the first issue from a Pydantic ``ValidationError`` as ``loc: msg``."""
    errs = exc.errors()
    if not errs:
        return str(exc)
    err = errs[0]
    loc = ".".join(str(part) for part in err.get("loc", ()))
    msg = err.get("msg", "invalid value")
    return f"{loc}: {msg}" if loc else msg
