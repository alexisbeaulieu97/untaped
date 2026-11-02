"""``--verbose`` debug logs: HTTP exchanges, with credentials masked (git runs: untaped-git)."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from untaped.sdk.network.http import HttpClient, RetryPolicy


@respx.mock
def test_http_logs_method_url_status_and_retries(caplog: pytest.LogCaptureFixture) -> None:
    respx.get("https://api.example.com/items").mock(
        side_effect=[httpx.Response(503, headers={"Retry-After": "0"}), httpx.Response(200)]
    )
    caplog.set_level(logging.DEBUG, logger="untaped")
    with HttpClient("https://user:hunter2@api.example.com", retry=RetryPolicy()) as client:
        client.get("/items")
    messages = [r.getMessage() for r in caplog.records if r.name == "untaped.http"]
    assert any(m.startswith("GET https://***@api.example.com/items -> 503") for m in messages)
    assert any(m.startswith("retrying in 0.0s (attempt 2 of 3)") for m in messages)
    assert any("-> 200 (" in m and m.endswith(" ms)") for m in messages)
    assert all("hunter2" not in m for m in messages)


def test_http_logs_nothing_unless_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="untaped")
    with respx.mock:
        respx.get("https://api.example.com/x").mock(return_value=httpx.Response(200))
        with HttpClient("https://api.example.com") as client:
            client.get("/x")
    assert not [r for r in caplog.records if r.name == "untaped.http"]
