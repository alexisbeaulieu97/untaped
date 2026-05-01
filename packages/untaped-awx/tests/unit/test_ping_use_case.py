from typing import Any

from untaped_awx.application import Ping
from untaped_awx.domain import PingStatus


class _StubClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def ping(self) -> dict[str, Any]:
        return self.payload


def test_validates_payload_into_domain_model() -> None:
    payload = {"version": "4.5", "active_node": "controller-1", "install_uuid": "abc"}
    use_case = Ping(_StubClient(payload))
    status = use_case()
    assert status == PingStatus(version="4.5", active_node="controller-1", install_uuid="abc")


def test_ignores_extra_fields() -> None:
    payload = {"version": "4.5", "active_node": "n1", "ha": True, "instances": []}
    status = Ping(_StubClient(payload))()
    assert status.version == "4.5"
    assert status.active_node == "n1"
