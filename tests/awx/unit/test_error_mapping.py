"""How AWX HTTP failures map onto typed errors and user-facing messages."""

from __future__ import annotations

import pytest

from untaped.capabilities.awx.errors import (
    AwxApiError,
    BadRequestError,
    ConflictError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from untaped.capabilities.awx.infrastructure.errors import map_awx_errors, to_awx_error
from untaped.capability_api import ConfigError, HttpError, UntapedError

_TOKEN_HINT = "hint: run `untaped config set awx.token --prompt`"


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (401, '{"detail":"invalid token"}', ConfigError, _TOKEN_HINT),
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
        assert mapped.status == mapped.status_code == status
    with pytest.raises(error), map_awx_errors():
        raise raw


def test_resource_not_found_message_includes_identity() -> None:
    err = ResourceNotFoundError("JobTemplate", {"name": "deploy", "organization": "Default"})
    assert "JobTemplate" in str(err)
    assert "deploy" in str(err)
