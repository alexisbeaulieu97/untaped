"""Base exception hierarchy, failure attribution, and process exit codes for untaped.

Every user-facing failure is an :class:`UntapedError`. Each one carries a
:class:`ErrorCategory` (what kind of failure it is, which selects the exit code
and whether a retry can help), a ``system`` (who is responsible: ``untaped``,
``local``, ``git``, or a service section such as ``awx``), an optional ``hint``
and a ``details`` mapping. Classes declare defaults; an instance overrides them
with keyword arguments. Core's error boundary exits with the error's
``exit_code``, so the exit-code contract (:class:`ExitCode`) lives next
to the exceptions that select it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from untaped.runtime.redaction import redact_url_password
from untaped.sdk._run import NoRunError, current_run

if TYPE_CHECKING:
    from pydantic import BaseModel, ValidationError

    from untaped.sdk.plugin import SpelledCommand


class ExitCode(IntEnum):
    """The suite's process exit codes (one meaning each).

    Each member's ``description`` is its meaning in one line, read at run
    time (``untaped docs`` lists them); attribute docstrings are not.
    """

    description: str

    def __new__(cls, value: int, description: str) -> ExitCode:
        member = int.__new__(cls, value)
        member._value_ = value
        member.description = description
        return member

    OK = 0, "Success."
    FAILURE = 1, "Runtime failure, a failed item, or a declined confirmation."
    USAGE = 2, "Bad invocation, detected before any side effect."
    PREDICATE = 3, "A predicate hit: ``--check`` drift, ``--fail-on-match``, ``--strict``."
    ENVIRONMENT = 4, "The environment needs fixing: config, credentials, permissions."
    UNAVAILABLE = 5, "A temporary failure (network, timeout, 5xx, 429, a held lock); retry later."
    INTERRUPTED = 130, "Interrupted (Ctrl-C), including at a prompt."


#: Exit codes from most to least severe: the first one seen in a run wins.
EXIT_CODE_PRECEDENCE: tuple[int, ...] = (
    ExitCode.INTERRUPTED,
    ExitCode.USAGE,
    ExitCode.ENVIRONMENT,
    ExitCode.UNAVAILABLE,
    ExitCode.FAILURE,
    ExitCode.PREDICATE,
    ExitCode.OK,
)


def _run_hint(command: SpelledCommand) -> str:
    from untaped.sdk.words import run_hint  # noqa: PLC0415 - only a spelled hint pays

    return run_hint(command)


def combine_exit_codes(*codes: int) -> int:
    """The exit code a run with these outcomes ends with: 130 > 2 > 4 > 5 > 1 > 3 > 0.

    An unknown non-zero code ranks as a plain failure (``1``). No codes is ``0``.
    """

    def rank(code: int) -> int:
        if code in EXIT_CODE_PRECEDENCE:
            return EXIT_CODE_PRECEDENCE.index(code)
        return EXIT_CODE_PRECEDENCE.index(ExitCode.FAILURE if code else ExitCode.OK)

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


FixBy = Literal["user"]
"""Who must take an error's hint step when an agent may not: only ``"user"`` exists."""


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


class System(StrEnum):
    """Who is responsible for a failure, when it is not a plugin's own service.

    ``UNTAPED`` is untaped itself, ``LOCAL`` the user's files and machine,
    ``GIT`` git, ``HTTP`` a server reached over HTTP. A plugin's own service
    is :class:`Service`.
    """

    UNTAPED = "untaped"
    LOCAL = "local"
    GIT = "git"
    HTTP = "http"


class Service:
    """A plugin's own service as an error's ``system``: ``system = Service(AwxSettings)``.

    It names the plugin through its settings model, so the error follows a
    renamed plugin. An error resolves it when it is raised, inside the run,
    and keeps the name, so it still reads after the run ends; read on the
    class, it is the plugin's name in the current run. With no run, or no
    composed plugin owning the model, it reads as its repr, never raising:
    an error's attribution must not fail.
    """

    __slots__ = ("model",)

    def __init__(self, model: type[BaseModel]) -> None:
        self.model = model

    def __get__(self, instance: object, owner: type | None = None) -> str:
        return self.name()

    def name(self) -> str:
        """The plugin's name in the current run, else ``Service(Model)``."""
        try:
            return current_run().composition.owner_of_settings(self.model)
        except NoRunError, LookupError:
            return repr(self)

    def __repr__(self) -> str:
        return f"Service({self.model.__qualname__})"


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
    system: str = System.UNTAPED
    """Who is responsible: a :class:`System`, or a plugin's :class:`Service`."""
    hint: str | None = None
    """A follow-up for the user, such as ``run `untaped …```, without a ``hint:`` prefix.

    ``hint=Set.spelled(key(...), "URL")`` names the command by its class: it
    reads ``run `untaped config set …``` as this run spells it."""
    details: Mapping[str, object] = _NO_DETAILS
    """Machine-readable context: ``status``, ``url``, related ids."""
    fix_by: FixBy | None = None
    """``"user"`` when only the person can take the hint's step (a login, a secret
    variable): an agent reports it and does not run it."""

    def __init__(
        self,
        *args: object,
        category: ErrorCategory | str | None = None,
        system: str | Service | None = None,
        hint: str | SpelledCommand | None = None,
        details: Mapping[str, object] | None = None,
        fix_by: FixBy | None = None,
    ) -> None:
        super().__init__(*args)
        if category is not None:
            self.category = ErrorCategory(category)
        # Kept on the instance: a Service names its plugin now, inside the run.
        self.system = system.name() if isinstance(system, Service) else system or self.system
        if hint is not None:
            self.hint = hint if isinstance(hint, str) else _run_hint(hint)
        if details:
            self.details = dict(details)
        if fix_by is not None:
            self.fix_by = fix_by

    @property
    def exit_code(self) -> int:
        """The process exit code core exits with for this error."""
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


def fix_by(error: BaseException) -> FixBy | None:
    """Who must take ``error``'s hint step: its own ``fix_by``, else its cause's.

    A replacement made with :func:`attribution` and ``raise … from exc``
    keeps the caught error's hint, so it keeps whose step that hint is
    without its constructor taking ``fix_by``.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while isinstance(current, UntapedError) and id(current) not in seen:
        if current.fix_by is not None:
            return current.fix_by
        seen.add(id(current))
        cause = current.__cause__
        if not isinstance(cause, UntapedError) or cause.hint != current.hint:
            return None
        current = cause
    return None


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


class InvalidValue(ValueError):
    """A validator's refusal naming commands by their class, reported as any ``ValueError``.

    ``InvalidValue("a Git URL must not carry credentials; use `{}`", Login.spelled())``
    fills each ``{}`` with a command line (:func:`~untaped.sdk.words.command_line`)
    whenever its text is read, so the run that reports it spells the command
    as it mounts it; other braces are text. A validator can run outside any
    run, where nothing can be spelled: there a command reads as its class name.
    """

    def __init__(self, text: str, *commands: SpelledCommand) -> None:
        super().__init__(text, *commands)
        self.parts = text.split("{}")
        if len(self.parts) != len(commands) + 1:
            raise TypeError(
                f"InvalidValue's text has {len(self.parts) - 1} {{}} for {len(commands)} commands"
            )
        self.commands = commands

    def __str__(self) -> str:
        from untaped.sdk.words import command_line  # noqa: PLC0415 - only spelling pays

        lines = []
        for command in self.commands:
            try:
                lines.append(command_line(command))
            except RuntimeError, LookupError:
                lines.append(command.command.__name__)
        return "".join(part + line for part, line in zip(self.parts, [*lines, ""], strict=True))


class NeedsYesError(UsageError):
    """A command that asks first ran without a terminal and without ``--yes``; exits ``2``.

    The message names the command and its question, which carries the reason. Raise it
    yourself for an early refusal before costly work; ``self.confirm(...)`` raises it otherwise.
    """

    def __init__(self, question: str, *, command: str = "this command") -> None:
        super().__init__(
            f'{command} requires --yes when not interactive: it asks "{question}"',
            hint="pass --yes to go ahead, or --dry-run to preview",
            details={"command": command, "question": question},
        )


class OperationCancelledError(UntapedError):
    """Raised when the user declines a confirmation; exits ``1``.

    The default message is the suite's standard decline line, so callers
    raise it bare: ``raise OperationCancelledError``.
    """

    def __init__(self, message: str = "cancelled; no changes made") -> None:
        super().__init__(message)


class Failed(UntapedError):
    """Raised after the rows when the command failed in a way its rows do not say: exit ``1``.

    For a command whose rows carry no failure but whose outcome is one (a
    check that failed, a job that ended badly): ``raise Failed()`` once every
    row is printed. Nothing more is printed; a failure the rows counted (a
    rejected token, ``4``) still sets the exit code.
    """

    def __init__(self, message: str = "the command failed") -> None:
        super().__init__(message)


class Matched(UntapedError):
    """Raised when a check matched: exit ``3``.

    For ``--check`` drift, ``--fail-on-match`` and ``--strict``. Nothing more
    is printed, and a failure the run counted wins over it. Let it reach
    core: a handler that catches every ``UntapedError`` (to turn it into a
    row) takes it for a failure, exit ``1``.
    """

    def __init__(self, message: str = "the check matched") -> None:
        super().__init__(message)

    @property
    def exit_code(self) -> int:
        return ExitCode.PREDICATE


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
    :mod:`untaped.sdk.network.http`) so a multi-MB proxy error page doesn't
    live on the exception — and through core's error boundary to stderr —
    long after the underlying response is collected.

    An error status (``>= 400``) selects the category unless one is given
    (:func:`category_for_status`); ``status`` and ``url`` join ``details``.
    URL passwords are masked in the message, ``url`` and ``details``.
    ``system`` is the client's section (``connected_client(section=…)``),
    else ``http``. ``describes_body`` marks a subclass whose message
    already carries the body's gist, so :func:`untaped.sdk.diagnostic.format_error`
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
    """Format the first issue from a Pydantic ``ValidationError`` as ``loc: msg``.

    A validator's ``ValueError`` text appears without pydantic's ``Value error, `` prefix.
    """
    errs = exc.errors()
    if not errs:
        return str(exc)
    err = errs[0]
    loc = ".".join(str(part) for part in err.get("loc", ()))
    msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
    return f"{loc}: {msg}" if loc else msg
