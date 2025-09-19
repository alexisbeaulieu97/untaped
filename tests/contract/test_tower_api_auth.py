from __future__ import annotations

import json

import httpx
import pytest

from untaped_ansible.api.auth import TowerAuthApi
from untaped_ansible.api.base import TowerApiClient
from untaped_ansible.api.errors import TowerAuthenticationError


BASE_URL = "https://tower.example.com"


def make_client(transport: httpx.MockTransport) -> TowerApiClient:
    return TowerApiClient(base_url=BASE_URL, token=None, transport=transport)


@pytest.mark.contract
def test_auth_login_returns_token_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v2/authtoken/"
        assert "Authorization" not in request.headers
        body = json.loads(request.content)
        assert body == {"username": "admin", "password": "secret"}
        return httpx.Response(200, json={"token": "abc123", "expires": "2025-09-19T10:00:00Z"})

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    auth = TowerAuthApi(client)

    result = auth.login(username="admin", password="secret")

    assert result["token"] == "abc123"
    assert "expires" in result


@pytest.mark.contract
def test_auth_login_raises_on_invalid_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid username/password"})

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    auth = TowerAuthApi(client)

    with pytest.raises(TowerAuthenticationError) as exc:
        auth.login(username="admin", password="wrong")

    assert exc.value.status_code == 401
