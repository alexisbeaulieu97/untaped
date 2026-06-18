"""Retry/backoff behaviour for :class:`HttpClient`.

Transport failures are retried by phase (pre-send connect failures for any
method, post-send read/write errors only for idempotent methods); 429/503 are
retried for idempotent methods, honouring a capped ``Retry-After``. ``_sleep``
is patched so these run instantly.
"""

from __future__ import annotations

import email.utils
import time

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from untaped.errors import HttpStatusError, HttpTransportError
from untaped.http import HttpClient, RetryPolicy, connected_client


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Capture the backoff delays without actually sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr("untaped.http._sleep", recorded.append)
    return recorded


class _DemoSettings(BaseModel):
    base_url: str = "https://api.example.com"
    token: SecretStr | None = None


def test_get_retries_on_429_then_succeeds(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/ping").mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": True})]
        )
        with HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client:
            assert client.get("/ping").json() == {"ok": True}
    assert route.call_count == 2
    assert len(no_sleep) == 1


def test_retry_after_seconds_header_is_honored(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        mock.get("/ping").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "2"}),
                httpx.Response(200, json={}),
            ]
        )
        with HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client:
            client.get("/ping")
    assert no_sleep == [2.0]


def test_retry_after_is_capped(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        mock.get("/ping").mock(
            side_effect=[
                httpx.Response(503, headers={"Retry-After": "9999"}),
                httpx.Response(200, json={}),
            ]
        )
        with HttpClient(
            base_url="https://example.com", retry=RetryPolicy(retry_after_max=5.0)
        ) as client:
            client.get("/ping")
    assert no_sleep == [5.0]


def test_retry_after_http_date_is_parsed(no_sleep: list[float]) -> None:
    future = email.utils.formatdate(time.time() + 30, usegmt=True)
    with respx.mock(base_url="https://example.com") as mock:
        mock.get("/ping").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": future}),
                httpx.Response(200, json={}),
            ]
        )
        with HttpClient(
            base_url="https://example.com", retry=RetryPolicy(retry_after_max=120.0)
        ) as client:
            client.get("/ping")
    assert len(no_sleep) == 1
    assert 1.0 < no_sleep[0] <= 120.0


def test_post_is_not_status_retried_by_default(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.post("/things").mock(return_value=httpx.Response(429))
        with (
            HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client,
            pytest.raises(HttpStatusError),
        ):
            client.post("/things")
    assert route.call_count == 1
    assert no_sleep == []


def test_post_is_status_retried_when_policy_opts_in(no_sleep: list[float]) -> None:
    policy = RetryPolicy(idempotent_methods=frozenset({"POST"}))
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.post("/search").mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": True})]
        )
        with HttpClient(base_url="https://example.com", retry=policy) as client:
            assert client.post("/search").json() == {"ok": True}
    assert route.call_count == 2


def test_presend_connect_error_is_retried_on_post(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.post("/things").mock(
            side_effect=[httpx.ConnectError("dns"), httpx.Response(201, json={"id": 1})]
        )
        with HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client:
            assert client.post("/things").json() == {"id": 1}
    assert route.call_count == 2


def test_postsend_read_timeout_is_not_retried_on_post(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.post("/things").mock(side_effect=httpx.ReadTimeout("slow"))
        with (
            HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client,
            pytest.raises(HttpTransportError),
        ):
            client.post("/things")
    assert route.call_count == 1
    assert no_sleep == []


def test_permanent_transport_error_is_not_retried(no_sleep: list[float]) -> None:
    """A permanent transport error (e.g. an unsupported URL scheme) is not
    transient — don't burn retries/backoff on it, even for an idempotent GET."""
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/x").mock(side_effect=httpx.UnsupportedProtocol("bad scheme"))
        with (
            HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client,
            pytest.raises(HttpTransportError),
        ):
            client.get("/x")
    assert route.call_count == 1
    assert no_sleep == []


def test_postsend_read_timeout_is_retried_on_get(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/things").mock(
            side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={"ok": True})]
        )
        with HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client:
            assert client.get("/things").json() == {"ok": True}
    assert route.call_count == 2


def test_max_attempts_exhausted_reraises(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/boom").mock(return_value=httpx.Response(503))
        with (
            HttpClient(base_url="https://example.com", retry=RetryPolicy(max_attempts=3)) as client,
            pytest.raises(HttpStatusError) as exc,
        ):
            client.get("/boom")
    assert exc.value.status_code == 503
    assert route.call_count == 3
    assert len(no_sleep) == 2


def test_per_call_retry_none_disables_inheritance(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/boom").mock(return_value=httpx.Response(503))
        with (
            HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client,
            pytest.raises(HttpStatusError),
        ):
            client.request("GET", "/boom", retry=None)
    assert route.call_count == 1
    assert no_sleep == []


def test_bare_client_does_not_retry(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/boom").mock(return_value=httpx.Response(503))
        with (
            HttpClient(base_url="https://example.com") as client,  # retry=None by default
            pytest.raises(HttpStatusError),
        ):
            client.get("/boom")
    assert route.call_count == 1


def test_connected_client_retries_by_default(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://api.example.com") as mock:
        route = mock.get("/user").mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json={"login": "x"})]
        )
        with connected_client(_DemoSettings(token=SecretStr("t")), section="demo") as client:
            assert client.get_json_dict("/user") == {"login": "x"}
    assert route.call_count == 2


def test_retry_policy_is_exported_from_package() -> None:
    from untaped import RetryPolicy as Exported

    assert Exported is RetryPolicy
