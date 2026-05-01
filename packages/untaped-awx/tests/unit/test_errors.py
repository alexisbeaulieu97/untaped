from __future__ import annotations

from untaped_awx.errors import (
    AwxApiError,
    Conflict,
    PermissionDenied,
    ResourceNotFound,
)
from untaped_core import UntapedError


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
