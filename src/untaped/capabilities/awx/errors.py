"""Typed exceptions for the AWX bounded context.

Concrete mapping from HTTP status to exception type lives in
``infrastructure.errors`` (it consumes the response body for actionable
messages). These types are surfaced to the CLI via
:func:`untaped.report_errors`. Every class is attributed to the ``awx``
system and declares the category (and so the exit code) of its failure;
an explicit HTTP ``status`` still selects the category of one instance.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping
from typing import Any

from untaped.sdk import (
    ErrorCategory,
    HttpError,
    UntapedError,
    not_found,
    q,
)


class AwxError(UntapedError):
    """Base class for every AWX capability error (a ``failed`` error in ``awx``)."""

    system = "awx"


class AwxApiError(AwxError, HttpError):
    """Raised when the AWX API returns an error or behaves unexpectedly.

    An :class:`HttpError`: ``status_code``, ``url`` and ``body`` name the
    failed response when there was one, and the status selects the category
    unless one is given. Its message already carries the body's gist (the
    mapper puts the first field error in it), so the raw body is shown only
    under ``--verbose``.
    """

    describes_body = True

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
        url: str | None = None,
        category: ErrorCategory | str | None = None,
        system: str | None = None,
        hint: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status,
            url=url,
            body=body,
            category=category,
            system=system,
            hint=hint,
            details=details,
        )


class ActionResponseError(AwxApiError):
    """A submitted action returned invalid data; retain only safe execution evidence."""

    category = ErrorCategory.FAILED

    def __init__(
        self,
        message: str,
        *,
        execution_id: int | None,
        execution_kind: str | None,
        category: ErrorCategory | str | None = None,
    ) -> None:
        super().__init__(message, category=category)
        self.execution_id = execution_id
        self.execution_kind = execution_kind


class PartialWriteError(AwxApiError):
    """A record write landed but a follow-up sub-document write for it failed.

    It is ``failed`` unless it carries the failed write's attribution
    (``**attribution(cause)``), e.g. ``auth`` for a rejected token.
    """

    category = ErrorCategory.FAILED

    def __init__(
        self,
        message: str,
        *,
        record_id: int,
        category: ErrorCategory | str | None = None,
        system: str | None = None,
        hint: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message, category=category, system=system, hint=hint, details=details)
        self.record_id = record_id


class LaunchPromptError(AwxApiError):
    """A launch supplies a field the template ignores, or omits a required survey var."""

    category = ErrorCategory.INVALID


class PendingApprovalError(AwxError):
    """A workflow under test waits on an approval its test case gives no answer for.

    ``node`` is the approval node's path (``outer/inner`` in a nested
    workflow), ``approval_id`` the waiting approval.
    """

    category = ErrorCategory.INVALID
    system = "awx.suite"

    def __init__(self, message: str, *, node: str, approval_id: int, hint: str) -> None:
        super().__init__(message, hint=hint)
        self.node = node
        self.approval_id = approval_id


class WaitCancelledError(AwxApiError):
    """A monitor's poll wait was interrupted (Ctrl-C); the execution keeps running."""

    category = ErrorCategory.INTERRUPTED


class BadRequestError(AwxApiError):
    """4xx response indicating malformed input (typically 400)."""

    category = ErrorCategory.INVALID


class PermissionDeniedError(AwxApiError):
    """403 — token authenticated but lacks the necessary permission."""

    category = ErrorCategory.PERMISSION


class ResourceNotFoundError(AwxApiError):
    """404 — looked-up resource does not exist."""

    category = ErrorCategory.NOT_FOUND

    def __init__(
        self,
        kind: str,
        identity: dict[str, Any],
        *,
        candidates: Iterable[str] = (),
        note: str | None = None,
        status: int | None = 404,
        body: str | None = None,
        url: str | None = None,
    ) -> None:
        candidates = tuple(candidates)
        super().__init__(
            _not_found_message(kind, identity, candidates, note),
            status=status,
            body=body,
            url=url,
        )
        self.kind = kind
        self.identity = identity
        self.candidates = candidates

    def with_note(self, note: str) -> ResourceNotFoundError:
        """This error with ``note`` as its second line (e.g. :func:`default_organization_note`)."""
        return ResourceNotFoundError(
            self.kind,
            self.identity,
            candidates=self.candidates,
            note=note,
            status=self.status_code,
            body=self.body,
            url=self.url,
        )


def default_organization_note(organization: str) -> str:
    """Say that ``awx.default_organization``, not a flag, scoped a name lookup."""
    return (
        f"searched in organization {q(organization)} (awx.default_organization); "
        "pass --organization to search elsewhere"
    )


def _not_found_message(
    kind: str, identity: dict[str, Any], candidates: Iterable[str], note: str | None
) -> str:
    """``<Kind> not found: 'x' in organization 'O'; did you mean 'y'?`` plus ``note``.

    Suggestions are the ``candidates`` (names in the searched scope) closest
    to the missing name. Identities without a name (an id lookup) keep the
    ``key=value`` listing.
    """
    if "name" not in identity:
        identity_str = ", ".join(f"{k}={v!r}" for k, v in identity.items())
        return f"{kind} not found ({identity_str})"
    scope = ", ".join(
        f"{key.replace('__', ' ').replace('_', ' ')} {q(value)}"
        for key, value in identity.items()
        if key != "name"
    )
    name = str(identity["name"])
    message = not_found(kind, name) + (f" in {scope}" if scope else "")
    suggestions = difflib.get_close_matches(name, list(dict.fromkeys(candidates)), n=3)
    if suggestions:
        message += f"; did you mean {', '.join(map(q, suggestions))}?"
    return f"{message}\n{note}" if note else message


class ConflictError(AwxApiError):
    """409 — resource state conflicts with the request (e.g. concurrent edit)."""

    category = ErrorCategory.CONFLICT


class MutationConflictError(AwxApiError):
    """Raised for an invalid no-create target or an unusable prepared plan."""

    category = ErrorCategory.INVALID


class AmbiguousIdentityError(AwxApiError):
    """Raised when an identity-by-name lookup matches more than one record.

    AWX scopes some names by organization or another parent. A query that
    drops the scope (or uses the wrong one) can match several records;
    silently picking the first one would target whichever the server
    happened to order ahead. Surface ambiguity instead so the caller can
    add the missing scope.
    """

    category = ErrorCategory.INVALID

    def __init__(
        self,
        kind: str,
        identity: dict[str, Any],
        *,
        match_count: int | None = None,
    ) -> None:
        # AWX's `<key>__name` filter syntax shouldn't leak into user messages.
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
