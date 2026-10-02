"""How AWX HTTP failures map onto typed errors and user-facing messages."""

from __future__ import annotations

import pytest

from untaped.capabilities.awx.errors import (
    ActionResponseError,
    AmbiguousIdentityError,
    AwxApiError,
    AwxError,
    BadRequestError,
    ConflictError,
    LaunchPromptError,
    MutationConflictError,
    PartialWriteError,
    PermissionDeniedError,
    ResourceNotFoundError,
    WaitCancelledError,
)
from untaped.capabilities.awx.infrastructure.errors import map_awx_errors, to_awx_error
from untaped.sdk import (
    ConfigError,
    ErrorCategory,
    HttpError,
    HttpStatusError,
    HttpTransportError,
    UntapedError,
)

_TOKEN_HINT = "hint: run `untaped config set awx.token --prompt`"


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (401, '{"detail":"invalid token"}', ConfigError, "AWX rejected the token (HTTP 401)"),
        (403, '{"detail": "you may not"}', PermissionDeniedError, "you may not"),
        (404, "", AwxApiError, "not found: /x"),
        (409, '{"name": ["already exists"]}', ConflictError, "name: already exists"),
        (400, '{"playbook": ["This field is required."]}', BadRequestError, "playbook"),
        (503, "service unavailable", AwxApiError, "service unavailable"),
        # the body is capped upstream and may end mid-token: keep the raw snippet
        (400, '{"name": ["Already exi', BadRequestError, '{"name": ["Already exi'),
        (None, "", AwxApiError, "dns failure"),
    ],
)
def test_http_errors_map_to_awx_errors(
    status: int | None, body: str, error: type[UntapedError], message: str
) -> None:
    summary = "dns failure" if status is None else f"HTTP {status} for /x"
    raw = HttpError(summary, status_code=status, url="/x", body=body)
    mapped = to_awx_error(raw)
    assert type(mapped) is error
    assert message in str(mapped)
    if isinstance(mapped, AwxApiError):
        assert mapped.status_code == status
        assert not hasattr(mapped, "status")
    with pytest.raises(error), map_awx_errors():
        raise raw


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, ErrorCategory.AUTH),
        (403, ErrorCategory.PERMISSION),
        (404, ErrorCategory.NOT_FOUND),
        (409, ErrorCategory.CONFLICT),
        (400, ErrorCategory.INVALID),
        (429, ErrorCategory.UNAVAILABLE),
        (503, ErrorCategory.UNAVAILABLE),
    ],
)
def test_mapped_errors_keep_the_http_attribution(status: int, category: ErrorCategory) -> None:
    raw = HttpStatusError(
        f"HTTP {status} for /x", status_code=status, url="/x", body="", system="awx"
    )
    mapped = to_awx_error(raw)
    assert mapped.category is category
    assert mapped.system == "awx"
    assert mapped.details["status"] == status
    assert mapped.details["url"] == "/x"


def test_rejected_token_stays_a_config_error_with_its_hint_as_a_field() -> None:
    raw = HttpStatusError("HTTP 401 for /me/", status_code=401, url="/me/", system="awx")
    mapped = to_awx_error(raw)
    assert isinstance(mapped, ConfigError)
    assert str(mapped) == "AWX rejected the token (HTTP 401)"
    assert mapped.hint == _TOKEN_HINT.removeprefix("hint: ")
    assert mapped.exit_code == 4


def test_transport_failures_stay_unavailable() -> None:
    raw = HttpTransportError("connection refused", url="/x", system="awx")
    mapped = to_awx_error(raw)
    assert isinstance(mapped, AwxApiError)
    assert mapped.category is ErrorCategory.UNAVAILABLE
    assert mapped.retryable
    assert mapped.exit_code == 5


def test_nested_mapping_keeps_the_typed_error() -> None:
    raw = HttpStatusError("HTTP 409 for /x", status_code=409, url="/x", body='{"name": ["taken"]}')
    with pytest.raises(ConflictError) as caught, map_awx_errors(), map_awx_errors():
        raise raw
    assert caught.value.__cause__ is raw


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (AwxError("x"), ErrorCategory.FAILED),
        (AwxApiError("x"), ErrorCategory.FAILED),
        (BadRequestError("x"), ErrorCategory.INVALID),
        (PermissionDeniedError("x"), ErrorCategory.PERMISSION),
        (ResourceNotFoundError("JobTemplate", {"name": "x"}), ErrorCategory.NOT_FOUND),
        (ResourceNotFoundError("JobTemplate", {"name": "x"}, status=None), ErrorCategory.NOT_FOUND),
        (ConflictError("x"), ErrorCategory.CONFLICT),
        (MutationConflictError("x"), ErrorCategory.INVALID),
        (AmbiguousIdentityError("Host", {"name": "x"}), ErrorCategory.INVALID),
        (LaunchPromptError("x"), ErrorCategory.INVALID),
        (WaitCancelledError("x"), ErrorCategory.INTERRUPTED),
        (PartialWriteError("x", record_id=1), ErrorCategory.FAILED),
        (ActionResponseError("x", execution_id=None, execution_kind=None), ErrorCategory.FAILED),
        # an explicit status still selects the category
        (BadRequestError("x", status=503), ErrorCategory.UNAVAILABLE),
    ],
)
def test_awx_errors_declare_a_category_in_awx(error: AwxError, category: ErrorCategory) -> None:
    assert error.category is category
    assert error.system == "awx"


def test_api_errors_are_http_errors_that_describe_their_body() -> None:
    error = BadRequestError("HTTP 400: name: taken", status=400, body='{"name": ["taken"]}')
    assert isinstance(error, HttpError)
    assert error.status_code == 400
    assert error.describes_body


def test_resource_not_found_message_includes_identity() -> None:
    err = ResourceNotFoundError("JobTemplate", {"name": "deploy", "organization": "Default"})
    assert "JobTemplate" in str(err)
    assert "deploy" in str(err)
