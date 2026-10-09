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
from untaped.http import HttpClient, RetryPolicy, connected_client, paginate_offset


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Capture the backoff delays without actually sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr("untaped.http._sleep", recorded.append)
    return recorded


class _DemoSettings(BaseModel):
    base_url: str = "https://api.example.com"
    token: SecretStr | None = None


_POST_OK = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "POST"})


@pytest.mark.parametrize(
    ("method", "policy", "first"),
    [
        ("GET", RetryPolicy(), httpx.Response(429)),
        ("POST", RetryPolicy(idempotent_methods=_POST_OK), httpx.Response(429)),
        # Pre-send connect failures are safe to retry for any method.
        ("POST", RetryPolicy(), httpx.ConnectError("dns")),
        # Post-send read errors are retried only for idempotent methods.
        ("GET", RetryPolicy(), httpx.ReadTimeout("slow")),
    ],
    ids=["get-429", "post-429-opt-in", "post-connect-error", "get-read-timeout"],
)
def test_transient_failure_is_retried_then_succeeds(
    no_sleep: list[float], method: str, policy: RetryPolicy, first: object
) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.route(method=method, path="/x").mock(
            side_effect=[first, httpx.Response(200, json={"ok": True})]
        )
        with HttpClient(base_url="https://example.com", retry=policy) as client:
            assert getattr(client, method.lower())("/x").json() == {"ok": True}
    assert route.call_count == 2
    assert len(no_sleep) == 1


@pytest.mark.parametrize(
    ("client_policy", "method", "outcome", "per_call", "error"),
    [
        (RetryPolicy(), "POST", httpx.Response(429), {}, HttpStatusError),
        (RetryPolicy(), "POST", httpx.ReadTimeout("slow"), {}, HttpTransportError),
        # A permanent transport error (e.g. an unsupported URL scheme) is not
        # transient — don't burn retries on it, even for an idempotent GET.
        (RetryPolicy(), "GET", httpx.UnsupportedProtocol("bad scheme"), {}, HttpTransportError),
        (RetryPolicy(), "GET", httpx.Response(503), {"retry": None}, HttpStatusError),
        (None, "GET", httpx.Response(503), {}, HttpStatusError),
    ],
    ids=[
        "post-status",
        "post-read-timeout",
        "permanent-transport",
        "per-call-retry-none",
        "bare-client",
    ],
)
def test_failure_is_not_retried(
    no_sleep: list[float],
    client_policy: RetryPolicy | None,
    method: str,
    outcome: object,
    per_call: dict[str, object],
    error: type[Exception],
) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.route(method=method, path="/x").mock(side_effect=[outcome])
        with (
            HttpClient(base_url="https://example.com", retry=client_policy) as client,
            pytest.raises(error),
        ):
            getattr(client, method.lower())("/x", **per_call)
    assert route.call_count == 1
    assert no_sleep == []


@pytest.mark.parametrize(
    ("retry_after", "policy", "expected"),
    [("2", RetryPolicy(), 2.0), ("9999", RetryPolicy(retry_after_max=5.0), 5.0)],
    ids=["honored", "capped"],
)
def test_retry_after_seconds_header(
    no_sleep: list[float], retry_after: str, policy: RetryPolicy, expected: float
) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        mock.get("/ping").mock(
            side_effect=[
                httpx.Response(503, headers={"Retry-After": retry_after}),
                httpx.Response(200, json={}),
            ]
        )
        with HttpClient(base_url="https://example.com", retry=policy) as client:
            client.get("/ping")
    assert no_sleep == [expected]


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


def test_connected_client_retries_by_default(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://api.example.com") as mock:
        route = mock.get("/user").mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json={"login": "x"})]
        )
        with connected_client(_DemoSettings(token=SecretStr("t")), section="demo") as client:
            assert client.get_json_dict("/user") == {"login": "x"}
    assert route.call_count == 2


def test_paginate_offset_post_retries_with_optin_policy(no_sleep: list[float]) -> None:
    """A per-call POST-inclusive policy lets an idempotent search page retry a
    429 — even though the client's default policy would not retry a POST."""
    policy = RetryPolicy(idempotent_methods=_POST_OK)
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.post("/search").mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(200, json={"issues": [{"id": "1"}], "isLast": True}),
            ]
        )
        with HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client:
            rows = list(
                paginate_offset(
                    client, "POST", "/search", item_key="issues", body={"jql": "x"}, retry=policy
                )
            )
    assert rows == [{"id": "1"}]
    assert route.call_count == 2
    assert len(no_sleep) == 1


def test_paginate_offset_post_inherits_client_policy_and_does_not_retry(
    no_sleep: list[float],
) -> None:
    """With the default ``_INHERIT``, the search page inherits the client's
    policy — which excludes POST — so a 429 raises without retrying."""
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.post("/search").mock(return_value=httpx.Response(429))
        with (
            HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client,
            pytest.raises(HttpStatusError),
        ):
            list(paginate_offset(client, "POST", "/search", item_key="issues", body={"jql": "x"}))
    assert route.call_count == 1
    assert no_sleep == []


def test_paginate_offset_get_retry_none_disables_client_policy(no_sleep: list[float]) -> None:
    """``retry=None`` reaches the GET page fetch and disables a retry the
    client's policy would otherwise apply to an idempotent GET."""
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/things").mock(return_value=httpx.Response(503))
        with (
            HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client,
            pytest.raises(HttpStatusError),
        ):
            list(paginate_offset(client, "GET", "/things", item_key="items", retry=None))
    assert route.call_count == 1
    assert no_sleep == []


def test_paginate_offset_forwards_retry_on_later_pages(no_sleep: list[float]) -> None:
    """The per-call policy is forwarded on every page, so a 429 on page 2 of a
    multi-page walk retries — not just the first fetch."""
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/things").mock(
            side_effect=[
                httpx.Response(200, json={"items": [{"i": 1}, {"i": 2}]}),  # full page, not last
                httpx.Response(429),  # page 2, retried
                httpx.Response(200, json={"items": [{"i": 3}], "isLast": True}),
            ]
        )
        # Bare client (retry=None); the explicit per-call policy is the only
        # thing that can make page 2 retry — proving it threads through the loop.
        with HttpClient(base_url="https://example.com") as client:
            rows = list(
                paginate_offset(
                    client, "GET", "/things", item_key="items", page_size=2, retry=RetryPolicy()
                )
            )
    assert [r["i"] for r in rows] == [1, 2, 3]
    assert route.call_count == 3
    assert len(no_sleep) == 1


def test_exhausted_retries_record_the_attempts_and_the_client_section(
    no_sleep: list[float],
) -> None:
    with respx.mock(base_url="https://api.example.com") as mock:
        mock.get("/x").mock(return_value=httpx.Response(503))
        client = connected_client(_DemoSettings(), section="demo", required=("base_url",))
        with client, pytest.raises(HttpStatusError) as exc_info:
            client.get("/x")
    error = exc_info.value
    assert (error.category, error.system, error.retryable) == ("unavailable", "demo", True)
    assert error.details == {"status": 503, "url": "https://api.example.com/x", "attempts": 3}


def test_a_transport_failure_is_unavailable_in_the_client_section(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://api.example.com") as mock:
        mock.get("/x").mock(side_effect=httpx.ConnectError("refused"))
        client = connected_client(_DemoSettings(), section="demo", required=("base_url",))
        with client, pytest.raises(HttpTransportError) as exc_info:
            client.get("/x")
    error = exc_info.value
    assert (error.category, error.system) == ("unavailable", "demo")
    assert error.details == {"url": "https://api.example.com/x", "attempts": 3}


def test_a_client_without_a_section_attributes_failures_to_http() -> None:
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
        pytest.raises(HttpStatusError) as exc_info,
    ):
        mock.get("/x").mock(return_value=httpx.Response(401))
        client.get("/x")
    assert (exc_info.value.category, exc_info.value.system) == ("auth", "http")


def test_a_deadline_caps_each_attempt_and_refuses_one_past_it(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    from untaped.http import request_deadline

    seen: list[object] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(503)

    clock = [100.0]
    monkeypatch.setattr("untaped.http.time.monotonic", lambda: clock[0])
    with respx.mock(base_url="https://example.com") as mock:
        mock.get("/x").mock(side_effect=record)
        with HttpClient(base_url="https://example.com", timeout=30.0) as client:
            with request_deadline(5.0), pytest.raises(HttpStatusError):
                client.get("/x", retry=RetryPolicy(max_attempts=2))
            assert seen == [5.0, 5.0]
            with request_deadline(60.0):
                clock[0] += 61
                with pytest.raises(HttpTransportError, match="deadline exceeded") as err:
                    client.get("/x")
    assert err.value.retryable


def test_a_nested_deadline_never_extends_the_outer_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from untaped.http import _time_left, request_deadline

    monkeypatch.setattr("untaped.http.time.monotonic", lambda: 0.0)
    assert _time_left() is None
    with request_deadline(5.0):
        with request_deadline(50.0):
            assert _time_left() == 5.0
        with request_deadline(None):
            assert _time_left() == 5.0
    assert _time_left() is None
