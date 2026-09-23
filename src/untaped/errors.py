"""Base exception hierarchy and process exit codes for untaped.

Every user-facing failure is an :class:`UntapedError`; its ``exit_code`` is
what :func:`untaped.cli.report_errors` exits with, so the exit-code contract
(:class:`ExitCode`) lives next to the exceptions that select it.
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pydantic import ValidationError


class ExitCode(IntEnum):
    """The suite's process exit codes (one meaning each)."""

    OK = 0
    """Success."""
    FAILURE = 1
    """Runtime failure, a failed item, or a declined confirmation."""
    USAGE = 2
    """Bad invocation, detected before any side effect."""
    PREDICATE = 3
    """A predicate hit: ``--check`` drift, ``--fail-on-match``, ``--strict``."""
    INTERRUPTED = 130
    """Interrupted (Ctrl-C), including at a prompt."""


class UntapedError(Exception):
    """Root of the untaped exception hierarchy.

    ``exit_code`` is the process exit code :func:`untaped.cli.report_errors`
    uses for this error class (``1`` unless a subclass says otherwise).
    """

    exit_code: ClassVar[int] = ExitCode.FAILURE


class ConfigError(UntapedError):
    """Raised when configuration is missing, malformed, or invalid."""


class UsageError(UntapedError):
    """Raised when a command is invoked wrongly; exits ``2``.

    Use it for problems detectable from the command line alone, before any
    side effect: conflicting or out-of-range flags, a missing required
    selection, an unknown column, "requires ``--yes`` when not interactive".
    Everything that depends on configuration or remote state stays a
    :class:`ConfigError` or another :class:`UntapedError` (exit ``1``).
    """

    exit_code: ClassVar[int] = ExitCode.USAGE


class OperationCancelledError(UntapedError):
    """Raised when the user declines a confirmation; exits ``1``.

    The default message is the suite's standard decline line, so callers
    raise it bare: ``raise OperationCancelledError``.
    """

    def __init__(self, message: str = "cancelled; no changes made") -> None:
        super().__init__(message)


class PromptInterruptedError(ConfigError):
    """Raised when a prompt is interrupted with Ctrl-C; exits ``130``.

    It stays a :class:`ConfigError` (its historical type) so callers that
    already handle prompt cancellation keep working; only the exit code says
    "interrupted".
    """

    exit_code: ClassVar[int] = ExitCode.INTERRUPTED


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
