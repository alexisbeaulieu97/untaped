"""Base exception hierarchy, failure attribution, and process exit codes for untaped.

Every user-facing failure is an :class:`UntapedError`. Each one carries a
:class:`ErrorCategory` (what kind of failure it is, which selects the exit code
and whether a retry can help), a ``system`` (who is responsible: ``untaped``,
``local``, ``git``, or a service section such as ``awx``), an optional ``hint``
and a ``details`` mapping. Classes declare defaults; an instance overrides them
with keyword arguments. :func:`untaped.cli.report_errors` exits with the
error's ``exit_code``, so the exit-code contract (:class:`ExitCode`) lives next
to the exceptions that select it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from untaped.redaction import redact_url_password

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
    ENVIRONMENT = 4
    """The environment needs fixing: config, credentials, permissions."""
    UNAVAILABLE = 5
    """A temporary failure (network, timeout, 5xx, 429); retry later."""
    INTERRUPTED = 130
    """Interrupted (Ctrl-C), including at a prompt."""


#: Exit codes from most to least severe: the first one seen in a run wins.
_PRECEDENCE: tuple[int, ...] = (
    ExitCode.INTERRUPTED,
    ExitCode.USAGE,
    ExitCode.ENVIRONMENT,
    ExitCode.UNAVAILABLE,
    ExitCode.FAILURE,
    ExitCode.PREDICATE,
    ExitCode.OK,
)


def combine_exit_codes(*codes: int) -> int:
    """The exit code a run with these outcomes ends with: 130 > 2 > 4 > 5 > 1 > 3 > 0.

    An unknown non-zero code ranks as a plain failure (``1``). No codes is ``0``.
    """

    def rank(code: int) -> int:
        if code in _PRECEDENCE:
            return _PRECEDENCE.index(code)
        return _PRECEDENCE.index(ExitCode.FAILURE if code else ExitCode.OK)

    return min(codes, key=rank, default=ExitCode.OK)


def most_severe[E: BaseException](errors: Iterable[E]) -> E:
    """The error whose exit code wins by precedence (the first one on a tie).

    Anything but an :class:`UntapedError` ranks as a plain failure (``1``).
    Raises :class:`ValueError` for no errors.
    """
    candidates = list(errors)
    if not candidates:
        raise ValueError("most_severe() needs at least one error")
    codes = [
        error.exit_code if isinstance(error, UntapedError) else ExitCode.FAILURE
        for error in candidates
    ]
    return candidates[codes.index(combine_exit_codes(*codes))]


class ErrorCategory(StrEnum):
    """What kind of failure an error is; it selects the exit code and retryability."""

    USAGE = "usage"
    """Bad flags or arguments, found before any side effect."""
    CONFIG = "config"
    """Local setup: missing or invalid settings, config file, CA bundle."""
    AUTH = "auth"
    """Credentials rejected (HTTP 401)."""
    PERMISSION = "permission"
    """Authenticated but not allowed (HTTP 403)."""
    NOT_FOUND = "not_found"
    """A named thing does not exist."""
    INVALID = "invalid"
    """The remote rejected our input (400/422), or a local input file is invalid."""
    CONFLICT = "conflict"
    """A concurrent change, or a name already taken (409)."""
    UNAVAILABLE = "unavailable"
    """Network down, timeout, 5xx, 429: temporary, worth retrying."""
    FAILED = "failed"
    """The operation ran and failed (a job, a test, a git command)."""
    INTERRUPTED = "interrupted"
    """Interrupted with Ctrl-C."""

    @property
    def exit_code(self) -> ExitCode:
        """The process exit code this category selects."""
        return _CATEGORY_EXIT_CODES[self]

    @property
    def retryable(self) -> bool:
        """Whether retrying the same command later can succeed."""
        return self is ErrorCategory.UNAVAILABLE


_CATEGORY_EXIT_CODES: Mapping[ErrorCategory, ExitCode] = MappingProxyType(
    {
        ErrorCategory.USAGE: ExitCode.USAGE,
        ErrorCategory.CONFIG: ExitCode.ENVIRONMENT,
        ErrorCategory.AUTH: ExitCode.ENVIRONMENT,
        ErrorCategory.PERMISSION: ExitCode.ENVIRONMENT,
        ErrorCategory.NOT_FOUND: ExitCode.FAILURE,
        ErrorCategory.INVALID: ExitCode.FAILURE,
        ErrorCategory.CONFLICT: ExitCode.FAILURE,
        ErrorCategory.UNAVAILABLE: ExitCode.UNAVAILABLE,
        ErrorCategory.FAILED: ExitCode.FAILURE,
        ErrorCategory.INTERRUPTED: ExitCode.INTERRUPTED,
    }
)

_NO_DETAILS: Mapping[str, object] = MappingProxyType({})


class UntapedError(Exception):
    """Root of the untaped exception hierarchy.

    ``category``, ``system``, ``hint`` and ``details`` default to the class's
    declaration (``failed`` in ``untaped`` unless a subclass says otherwise);
    pass them as keyword arguments to override one instance, e.g.
    ``ConfigError("token rejected", category="auth", system="awx")``.
    ``exit_code`` and ``retryable`` derive from ``category``.
    """

    category: ErrorCategory = ErrorCategory.FAILED
    """What kind of failure this is (selects the exit code)."""
    system: str = "untaped"
    """Who is responsible: ``untaped``, ``local``, ``git``, or a service section."""
    hint: str | None = None
    """A follow-up for the user, such as ``run `untaped …```, without a ``hint:`` prefix."""
    details: Mapping[str, object] = _NO_DETAILS
    """Machine-readable context: ``status``, ``url``, related ids."""

    def __init__(
        self,
        *args: object,
        category: ErrorCategory | str | None = None,
        system: str | None = None,
        hint: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(*args)
        if category is not None:
            self.category = ErrorCategory(category)
        if system is not None:
            self.system = system
        if hint is not None:
            self.hint = hint
        if details:
            self.details = dict(details)

    @property
    def exit_code(self) -> int:
        """The process exit code :func:`untaped.cli.report_errors` uses for this error."""
        return self.category.exit_code

    @property
    def retryable(self) -> bool:
        """Whether retrying later can succeed (only ``unavailable`` failures)."""
        return self.category.retryable


def attribution(error: BaseException) -> dict[str, Any]:
    """The ``category``/``system``/``hint``/``details`` keyword arguments of ``error``.

    Pass them on when a new error replaces a caught one, so the replacement
    keeps its attribution: ``raise AwxApiError(msg, **attribution(exc))``.
    ``hint`` and ``details`` are included only when set. Anything but an
    :class:`UntapedError` has none (``{}``).
    """
    if not isinstance(error, UntapedError):
        return {}
    fields: dict[str, Any] = {"category": error.category, "system": error.system}
    if error.hint:
        fields["hint"] = error.hint
    if error.details:
        fields["details"] = dict(error.details)
    return fields


class ConfigError(UntapedError):
    """Raised when configuration is missing, malformed, or invalid (exits ``4``).

    It means local setup needs fixing. An invalid *input* file (a resource
    or suite the command reads) is ``category="invalid"`` instead (exits ``1``).
    """

    category = ErrorCategory.CONFIG
    system = "local"


class UsageError(UntapedError):
    """Raised when a command is invoked wrongly; exits ``2``.

    Use it for problems detectable from the command line alone, before any
    side effect: conflicting or out-of-range flags, a missing required
    selection, an unknown column, "requires ``--yes`` when not interactive".
    Everything that depends on configuration or remote state stays a
    :class:`ConfigError` or another :class:`UntapedError`.
    """

    category = ErrorCategory.USAGE


class OperationCancelledError(UntapedError):
    """Raised when the user declines a confirmation; exits ``1``.

    The default message is the suite's standard decline line, so callers
    raise it bare: ``raise OperationCancelledError``.
    """

    def __init__(self, message: str = "cancelled; no changes made") -> None:
        super().__init__(message)


class PromptInterruptedError(UntapedError):
    """Raised when a prompt is interrupted with Ctrl-C; exits ``130``."""

    category = ErrorCategory.INTERRUPTED
    system = "untaped"


def category_for_status(status_code: int) -> ErrorCategory:
    """The category of an HTTP error status (``>= 400``)."""
    if status_code == 401:
        return ErrorCategory.AUTH
    if status_code == 403:
        return ErrorCategory.PERMISSION
    if status_code in (404, 410):
        return ErrorCategory.NOT_FOUND
    if status_code in (409, 412):
        return ErrorCategory.CONFLICT
    if status_code in (408, 429) or status_code >= 500:
        return ErrorCategory.UNAVAILABLE
    return ErrorCategory.INVALID


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

    An error status (``>= 400``) selects the category unless one is given
    (:func:`category_for_status`); ``status`` and ``url`` join ``details``.
    URL passwords are masked in the message, ``url`` and ``details``.
    ``system`` is the client's section (``connected_client(section=…)``),
    else ``http``. ``describes_body`` marks a subclass whose message
    already carries the body's gist, so :func:`untaped.cli.format_error`
    shows the raw body only under ``--verbose``.
    """

    system = "http"
    describes_body: bool = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        body: str | None = None,
        category: ErrorCategory | str | None = None,
        system: str | None = None,
        hint: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        if category is None and status_code is not None and status_code >= 400:
            category = category_for_status(status_code)
        url = redact_url_password(url) if url is not None else None
        known = {"status": status_code, "url": url}
        merged = {key: value for key, value in known.items() if value is not None}
        super().__init__(
            redact_url_password(message),
            category=category,
            system=system,
            hint=hint,
            details={**merged, **(details or {})},
        )
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
    """Raised when the request never produced a response (``unavailable``).

    Connection failures, timeouts, and DNS errors map here; ``status_code`` is
    ``None`` because no status was ever received. These are typically transient
    and worth retrying, unlike :class:`HttpStatusError`.
    """

    category = ErrorCategory.UNAVAILABLE


def first_validation_error(exc: ValidationError) -> str:
    """Format the first issue from a Pydantic ``ValidationError`` as ``loc: msg``."""
    errs = exc.errors()
    if not errs:
        return str(exc)
    err = errs[0]
    loc = ".".join(str(part) for part in err.get("loc", ()))
    msg = err.get("msg", "invalid value")
    return f"{loc}: {msg}" if loc else msg
