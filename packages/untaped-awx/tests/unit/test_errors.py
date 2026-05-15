from __future__ import annotations

from untaped_awx.errors import (
    AwxApiError,
    BadRequest,
    Conflict,
    PermissionDenied,
    ResourceNotFound,
)
from untaped_awx.infrastructure.errors import to_awx_error
from untaped_core import HttpError, UntapedError


def test_awx_api_error_is_untaped_error() -> None:
    err = AwxApiError("boom", status=500, body="server log")
    assert isinstance(err, UntapedError)
    assert err.status == 500
    assert err.body == "server log"


def test_resource_not_found_message_includes_identity() -> None:
    err = ResourceNotFound("JobTemplate", {"name": "deploy", "organization": "Default"})
    assert "JobTemplate" in str(err)
    assert "deploy" in str(err)
    assert err.kind == "JobTemplate"
    assert err.identity == {"name": "deploy", "organization": "Default"}


def test_conflict_and_permission_denied_subclass_awx_api_error() -> None:
    assert issubclass(Conflict, AwxApiError)
    assert issubclass(PermissionDenied, AwxApiError)


def test_to_awx_error_degrades_gracefully_on_body_truncated_mid_token() -> None:
    """`HttpError.body` is capped at ~2KB at the wrap site, so the body
    can land mid-JSON-token. `to_awx_error` must fall back to the raw
    snippet (no crash, no exception leak) rather than failing JSON
    parsing — pins the contract that lets `untaped-core` truncate
    without coordinating with `untaped-awx`.
    """
    truncated = '{"name": ["Already exi'  # cut mid-string
    err = HttpError("HTTP 400 for /api", status_code=400, url="/api", body=truncated)

    mapped = to_awx_error(err)

    assert isinstance(mapped, BadRequest)
    assert truncated in str(mapped)
