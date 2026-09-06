"""Auth regression guard: a configured token must reach AAP as a Bearer header.

awx leaves ``token`` out of ``connected_client``'s ``required`` so a token-less
client can hit unauthenticated endpoints (``ping/``). Regressed once: the SDK
only read fields listed in ``required``, so the configured token was never sent
— every authenticated request went out unauthenticated and AAP replied 401.
Fixed in untaped 1.1.1 (``connected_client`` always walks ``bearer_token_field``).
"""

from __future__ import annotations

import httpx
import respx

from untaped.capabilities.awx.infrastructure import AwxClient, AwxConfig


def test_awx_client_sends_bearer_token(awx_config: AwxConfig) -> None:
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0"})
        )
        with AwxClient(awx_config) as client:
            client.ping()

    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


def test_awx_client_without_token_sends_no_auth() -> None:
    config = AwxConfig(base_url="https://aap.example.com", api_prefix="/api/v2/")
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0"})
        )
        with AwxClient(config) as client:
            client.ping()

    assert "Authorization" not in route.calls.last.request.headers
