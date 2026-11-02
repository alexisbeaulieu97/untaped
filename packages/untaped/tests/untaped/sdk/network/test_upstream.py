"""The upstream budget: every call capped and unretried until one succeeds, then none.

``HttpClient`` reads it (untaped-git's tests cover ``run_git``); ``quick_probe``
(``doctor --online``) keeps every call capped. The back-off waits on the test's
clock, so retries run instantly.
"""

from __future__ import annotations

import threading

import httpx
import pytest
import respx

from untaped.sdk._concurrency import bounded_map
from untaped.sdk.errors import HttpStatusError, HttpTransportError
from untaped.sdk.network.http import HttpClient, RetryPolicy
from untaped.sdk.network.upstream import PROBE_TIMEOUT, capped, fail_fast, full_retries, quick_probe
from untaped.sdk.testing import ManualClock


@pytest.fixture
def no_sleep(clock: ManualClock) -> list[float]:
    return clock.slept


def _timeouts(route: respx.Route) -> list[float | None]:
    """The read timeout each call of ``route`` was sent with."""
    return [call.request.extensions["timeout"]["read"] for call in route.calls]


def test_no_budget_retries_in_full(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/x").mock(side_effect=[httpx.Response(503), httpx.Response(200)])
        with HttpClient(
            base_url="https://example.com", timeout=30.0, retry=RetryPolicy()
        ) as client:
            assert client.get("/x").status_code == 200
    assert route.call_count == 2
    assert _timeouts(route) == [30.0, 30.0]


def test_fail_fast_caps_every_call_until_one_succeeds_then_none(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        down = mock.get("/down").mock(return_value=httpx.Response(503))
        up = mock.get("/up").mock(return_value=httpx.Response(200))
        flaky = mock.get("/flaky").mock(side_effect=[httpx.Response(503), httpx.Response(200)])
        with (
            fail_fast(),
            HttpClient(base_url="https://example.com", timeout=30.0, retry=RetryPolicy()) as client,
        ):
            for _ in range(2):  # neither the first call nor the next is retried
                with pytest.raises(HttpStatusError):
                    client.get("/down")
            assert capped() == PROBE_TIMEOUT
            client.get("/up")
            assert capped() is None
            assert client.get("/flaky").status_code == 200  # retried: the cap is lifted
    assert down.call_count == 2
    assert _timeouts(down) == [PROBE_TIMEOUT, PROBE_TIMEOUT]
    assert _timeouts(up) == [PROBE_TIMEOUT]
    assert flaky.call_count == 2
    assert _timeouts(flaky) == [30.0, 30.0]
    assert capped() is None  # outside the block


def test_a_per_request_timeout_is_capped_too(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/x").mock(return_value=httpx.Response(200))
        with fail_fast(), HttpClient(base_url="https://example.com") as client:
            client.get("/x", timeout=120.0)
            client.get("/x", timeout=120.0)
    assert _timeouts(route) == [PROBE_TIMEOUT, 120.0]


def test_a_client_built_before_the_first_success_is_uncapped_after_it(
    no_sleep: list[float],
) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/x").mock(return_value=httpx.Response(200))
        with fail_fast():
            client = HttpClient(base_url="https://example.com", timeout=30.0, retry=RetryPolicy())
            client.get("/x")
            client.get("/x")
            client.close()
    assert _timeouts(route) == [PROBE_TIMEOUT, 30.0]


def test_quick_probe_caps_every_call_even_after_a_success(no_sleep: list[float]) -> None:
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/x").mock(return_value=httpx.Response(200))
        flaky = mock.get("/flaky").mock(side_effect=[httpx.Response(503), httpx.Response(200)])
        with (
            quick_probe(),
            HttpClient(base_url="https://example.com", timeout=30.0, retry=RetryPolicy()) as client,
        ):
            client.get("/x")
            assert capped() == PROBE_TIMEOUT
            with pytest.raises(HttpStatusError):
                client.get("/flaky", retry=RetryPolicy())
    assert _timeouts(route) == [PROBE_TIMEOUT]
    assert flaky.call_count == 1


def test_parallel_first_calls_offline_all_fail_capped(no_sleep: list[float]) -> None:
    """Eight workers in one block share its budget: offline, none of them retries."""
    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/x").mock(side_effect=httpx.ConnectError("offline"))
        failures: list[str] = []
        with (
            fail_fast(),
            HttpClient(base_url="https://example.com", timeout=30.0, retry=RetryPolicy()) as client,
        ):

            def probe(_item: int) -> str:
                try:
                    client.get("/x")
                except HttpTransportError as exc:
                    return str(exc.details["attempts"])
                return "ok"

            bounded_map(probe, range(8), concurrency=8, on_each=lambda _i, r: failures.append(r))
    assert failures == ["1"] * 8
    assert route.call_count == 8
    assert set(_timeouts(route)) == {PROBE_TIMEOUT}


def test_one_success_lifts_the_cap_for_every_worker(no_sleep: list[float]) -> None:
    """The cell is shared, not copied: a success in one worker reaches the others."""
    succeeded = threading.Event()
    with respx.mock(base_url="https://example.com") as mock:
        mock.get("/up").mock(return_value=httpx.Response(200))
        later = mock.get("/later").mock(return_value=httpx.Response(200))
        with (
            fail_fast(),
            HttpClient(base_url="https://example.com", timeout=30.0, retry=RetryPolicy()) as client,
        ):

            def call(item: int) -> None:
                if item == 0:
                    client.get("/up")
                    succeeded.set()
                else:
                    assert succeeded.wait(5)
                    client.get("/later")

            bounded_map(call, range(4), concurrency=4, on_each=lambda _i, _r: None)
    assert set(_timeouts(later)) == {30.0}


def test_full_retries_steps_out_of_fail_fast_but_not_out_of_quick_probe() -> None:
    with fail_fast():
        with full_retries():
            assert capped() is None
        assert capped() == PROBE_TIMEOUT
    with quick_probe(), full_retries():
        assert capped() == PROBE_TIMEOUT
