"""Auth regression guard: a configured token must reach AAP as a Bearer header.

A token-less awx client can hit unauthenticated endpoints (``ping/``).
Regressed once: the SDK only read the fields a token-less client required, so
the configured token was never sent: every authenticated request went out
unauthenticated and AAP replied 401. Fixed in untaped 1.1.1; a token found for
the server is always the bearer.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import HttpUrl

from awx_test_support import installed
from untaped.sdk import HttpFactory
from untaped_awx.infrastructure import AwxClient
from untaped_awx.settings import TOKEN, AwxServer, AwxSettings


def test_awx_client_sends_bearer_token(awx_config: AwxSettings, http: HttpFactory) -> None:
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0"})
        )
        with AwxClient(awx_config, http=http) as client:
            client.ping()

    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


def test_awx_client_without_token_sends_no_auth(
    http: HttpFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CONTROLLER_OAUTH_TOKEN")
    config = AwxSettings(
        server=AwxServer(url=HttpUrl("https://aap.example.com")), api_prefix="/api/v2/"
    )
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0"})
        )
        with AwxClient(config, http=http) as client:
            client.ping()

    assert "Authorization" not in route.calls.last.request.headers


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"CONTROLLER_OAUTH_TOKEN": "c", "TOWER_OAUTH_TOKEN": "t", "AAP_TOKEN": "a"}, "c"),
        ({"TOWER_OAUTH_TOKEN": "t", "AAP_TOKEN": "a"}, "t"),
        ({"AAP_TOKEN": "a"}, "a"),
    ],
)
def test_token_falls_back_to_the_controller_collection_variables(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    for name in ("CONTROLLER_OAUTH_TOKEN", "TOWER_OAUTH_TOKEN", "AAP_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    server = AwxServer(url=HttpUrl("https://aap.example.com"))
    with installed().running():
        assert TOKEN.get(server).get_secret_value() == expected
