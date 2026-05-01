from __future__ import annotations

import pytest
from untaped_awx.errors import AwxApiError, BadRequest, Conflict, PermissionDenied
from untaped_awx.infrastructure.errors import map_awx_errors, to_awx_error
from untaped_core import ConfigError, HttpError


def _http_error(status: int, body: str = "") -> HttpError:
    return HttpError(f"HTTP {status} for /x", status_code=status, url="/x", body=body)


def test_401_maps_to_config_error() -> None:
    err = to_awx_error(_http_error(401, '{"detail":"invalid token"}'))
    assert isinstance(err, ConfigError)
    assert "awx.token" in str(err)


def test_403_maps_to_permission_denied_with_body() -> None:
    err = to_awx_error(_http_error(403, '{"detail": "you may not"}'))
    assert isinstance(err, PermissionDenied)
    assert "you may not" in str(err)


def test_404_maps_to_generic_awx_api_error() -> None:
    err = to_awx_error(_http_error(404))
    assert isinstance(err, AwxApiError)
    assert err.status == 404


def test_409_maps_to_conflict() -> None:
    err = to_awx_error(_http_error(409, '{"name": ["already exists"]}'))
    assert isinstance(err, Conflict)
    assert "already exists" in str(err)


def test_400_maps_to_bad_request_with_field_error() -> None:
    err = to_awx_error(_http_error(400, '{"playbook": ["This field is required."]}'))
    assert isinstance(err, BadRequest)
    assert "playbook" in str(err)


def test_500_maps_to_awx_api_error_with_snippet() -> None:
    err = to_awx_error(_http_error(503, "service unavailable"))
    assert isinstance(err, AwxApiError)
    assert err.status == 503
    assert "service unavailable" in str(err)


def test_status_none_passes_through() -> None:
    err = to_awx_error(HttpError("dns failure", status_code=None))
    assert isinstance(err, AwxApiError)
    assert err.status is None


def test_map_awx_errors_context_manager() -> None:
    with pytest.raises(Conflict), map_awx_errors():
        raise _http_error(409, '{"name": ["dup"]}')
