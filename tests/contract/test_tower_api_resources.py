from __future__ import annotations

import httpx
import pytest

from untaped_ansible.api.base import TowerApiClient
from untaped_ansible.api.errors import TowerApiError
from untaped_ansible.api.resources import TowerResourcesApi


BASE_URL = "https://tower.example.com"


def make_client(transport: httpx.MockTransport) -> TowerApiClient:
    return TowerApiClient(base_url=BASE_URL, token="secret-token", transport=transport)


@pytest.mark.contract
def test_lookup_inventory_by_name_returns_first_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v2/inventories/"
        assert str(request.url.params) == "name=Demo+Inventory"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "results": [
                    {"id": 10, "name": "Demo Inventory"},
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    api = TowerResourcesApi(client)

    inventory_id = api.get_inventory_id("Demo Inventory")

    assert inventory_id == 10


@pytest.mark.contract
def test_lookup_project_raises_error_when_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 0, "results": []})

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    api = TowerResourcesApi(client)

    with pytest.raises(TowerApiError) as exc:
        api.get_project_id("Missing Project")

    assert "Missing Project" in str(exc.value)
