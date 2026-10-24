"""The run's clock: cache ages, progress cadence and HTTP back-off all read it.

Outside a run, blocks read the clock installed outside one (a plain clock
unless a test installs its own); a run starts with that clock. A test's
:class:`~untaped.sdk.testing.ManualClock` moves all three at once and never
waits.
"""

from __future__ import annotations

import email.utils
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from untaped.sdk.network.http import HttpClient, RetryPolicy
from untaped.sdk.run import Clock, Composition, Run, installed_clock, run_clock, run_scope
from untaped.sdk.storage import cache as cache_module
from untaped.sdk.testing import ManualClock, cache_clock, manual_clock, progress_clock

START = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _run(home: Path, **clock: Clock) -> Run:
    return Run(
        home=home,
        composition=Composition(),
        picks=lambda _section, model: model(),
        diag=None,  # type: ignore[arg-type]
        terminal=lambda: pytest.fail("no terminal in this test"),
        **clock,  # type: ignore[arg-type]
    )


def test_outside_a_run_blocks_read_a_plain_clock_until_one_is_installed() -> None:
    plain = run_clock()
    assert type(plain) is Clock
    mine = ManualClock(START)
    with installed_clock(mine):
        assert run_clock() is mine
    assert run_clock() is plain


def test_a_run_starts_with_the_clock_outside_and_keeps_its_own(tmp_path: Path) -> None:
    with manual_clock(START) as clock:
        run = _run(tmp_path)
    assert run.clock is clock
    with run_scope(run):
        assert run_clock() is clock
    own = ManualClock(START)
    with run_scope(_run(tmp_path, clock=own)):
        assert run_clock() is own


def test_advance_moves_wall_and_monotonic_time_together() -> None:
    clock = ManualClock(START)
    before = clock.monotonic()
    clock.advance(90)
    clock.advance(timedelta(minutes=1), hours=1)
    assert clock.now() == START + timedelta(hours=1, minutes=2, seconds=30)
    assert clock.monotonic() - before == 3750.0


def test_a_naive_start_is_utc_and_none_is_now_to_the_second() -> None:
    assert ManualClock(datetime(2026, 10, 24, 12)).now() == START
    now = ManualClock().now()
    assert now.tzinfo is UTC
    assert now.microsecond == 0


def test_a_sleep_moves_the_clock_and_never_waits() -> None:
    clock = ManualClock(START)
    clock.sleep(3600)
    assert clock.slept == [3600]
    assert clock.now() == START + timedelta(hours=1)


def test_http_back_off_sleeps_on_the_clock_and_reads_retry_after_from_it() -> None:
    with manual_clock(START) as clock:
        when = email.utils.format_datetime(clock.now() + timedelta(seconds=3), usegmt=True)
        with respx.mock(base_url="https://example.com") as mock:
            mock.get("/x").mock(
                side_effect=[
                    httpx.Response(503, headers={"Retry-After": when}),
                    httpx.Response(200, json={"ok": True}),
                ]
            )
            with HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client:
                assert client.get("/x").json() == {"ok": True}
    assert clock.slept == [3.0]


def test_caches_read_their_ages_from_the_clock() -> None:
    with manual_clock(START) as clock:
        assert cache_module._utcnow() == START
        clock.advance(hours=2)
        assert cache_module._utcnow() == START + timedelta(hours=2)


def test_the_one_half_clocks_keep_the_other_half_of_the_clock_below() -> None:
    with manual_clock(START) as clock:
        with progress_clock() as progress:
            progress.advance(5)
            assert run_clock().monotonic() == 5
            assert run_clock().now() == START
        with cache_clock(START + timedelta(days=1)):
            assert run_clock().now() == START + timedelta(days=1)
            clock.sleep(2)
            assert run_clock().monotonic() == 2
    assert clock.slept == [2]
