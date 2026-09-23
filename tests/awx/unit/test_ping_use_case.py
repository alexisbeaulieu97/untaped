from typing import Any

import pytest

from untaped.capabilities.awx.application import Ping
from untaped.capabilities.awx.domain import PingStatus


class _StubClient:
    def __init__(
        self, payload: dict[str, Any], me: dict[str, Any] | Exception | None = None
    ) -> None:
        self.payload = payload
        self._me = me if me is not None else {"results": [{"username": "admin"}]}

    def ping(self) -> dict[str, Any]:
        return self.payload

    def me(self) -> dict[str, Any]:
        if isinstance(self._me, Exception):
            raise self._me
        return self._me


def test_validates_payload_into_domain_model() -> None:
    payload = {"version": "4.5", "active_node": "controller-1", "install_uuid": "abc"}
    use_case = Ping(_StubClient(payload))
    status = use_case()
    assert status == PingStatus(
        version="4.5", active_node="controller-1", install_uuid="abc", user="admin"
    )


def test_ignores_extra_fields() -> None:
    payload = {"version": "4.5", "active_node": "n1", "ha": True, "instances": []}
    status = Ping(_StubClient(payload))()
    assert status.version == "4.5"
    assert status.active_node == "n1"


def test_authentication_failure_propagates() -> None:
    from untaped.api import ConfigError

    payload = {"version": "4.5", "active_node": "n1"}
    with pytest.raises(ConfigError):
        Ping(_StubClient(payload, me=ConfigError("HTTP 401")))()
