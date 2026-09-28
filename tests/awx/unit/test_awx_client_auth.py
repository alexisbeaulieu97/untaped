"""Auth regression guard: a configured token must reach AAP as a Bearer header.

awx leaves ``token`` out of ``connected_client``'s ``required`` so a token-less
client can hit unauthenticated endpoints (``ping/``). Regressed once: the SDK
only read fields listed in ``required``, so the configured token was never sent
— every authenticated request went out unauthenticated and AAP replied 401.
Fixed in untaped 1.1.1 (``connected_client`` always walks ``bearer_token_field``).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.awx.infrastructure import AwxClient
from untaped.capabilities.awx.settings import AwxSettings
from untaped.capability_api import get_config_section


def test_awx_client_sends_bearer_token(awx_config: AwxSettings) -> None:
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0"})
        )
        with AwxClient(awx_config) as client:
            client.ping()

    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


def test_awx_client_without_token_sends_no_auth() -> None:
    config = AwxSettings(base_url="https://aap.example.com", api_prefix="/api/v2/")
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0"})
        )
        with AwxClient(config) as client:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    config = tmp_path / "config.yml"
    config.write_text("profiles:\n  default:\n    awx:\n      base_url: https://aap.example.com\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(config))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    token = get_config_section("awx", AwxSettings).token
    assert token is not None
    assert token.get_secret_value() == expected
