"""The run's clock: cache ages, progress cadence and HTTP back-off all read it.

Blocks read the current run's clock, else a plain one; a test's installed
clock wins over both, in every thread. A
:class:`~untaped.sdk.testing.ManualClock` moves all three at once and never
waits.
"""

from __future__ import annotations

import email.utils
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from cyclopts import App

from untaped.runtime.sink import Diagnostics
from untaped.sdk._concurrency import bounded_map
from untaped.sdk.network.http import HttpClient, RetryPolicy, _time_left, request_deadline
from untaped.sdk.run import Clock, Composition, Run, installed_clock, run_clock, run_scope
from untaped.sdk.storage import cache as cache_module
from untaped.sdk.testing import (
    ManualClock,
    cache_clock,
    invoke_cli,
    manual_clock,
    progress_clock,
)

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


def test_blocks_read_the_runs_clock_else_a_plain_one(tmp_path: Path) -> None:
    plain = run_clock()
    assert type(plain) is Clock
    own = ManualClock(START)
    with run_scope(_run(tmp_path, clock=own)):
        assert run_clock() is own
    assert run_clock() is plain


def test_an_installed_clock_wins_over_any_run_and_restores_in_order(tmp_path: Path) -> None:
    outer, inner = ManualClock(START), ManualClock(START)
    with run_scope(_run(tmp_path, clock=ManualClock())), installed_clock(outer):
        assert run_clock() is outer
        with pytest.raises(RuntimeError), installed_clock(inner):
            assert run_clock() is inner
            raise RuntimeError
        assert run_clock() is outer
        with cache_clock(datetime(2000, 1, 1)):
            assert cache_module._utcnow() == datetime(2000, 1, 1, tzinfo=UTC)


def test_a_worker_reads_the_installed_clock_and_the_runs(tmp_path: Path) -> None:
    def read(_item: int) -> Clock:
        return run_clock()

    def ran() -> list[Clock]:
        seen: list[Clock] = []
        bounded_map(read, [1, 2], concurrency=2, on_each=lambda _i, clock: seen.append(clock))
        return seen

    own = ManualClock(START)
    with run_scope(_run(tmp_path, clock=own)):
        assert ran() == [own, own]
    with manual_clock(START) as clock:
        assert ran() == [clock, clock]


def test_advance_moves_wall_and_monotonic_time_together() -> None:
    clock = ManualClock(START)
    before = clock.monotonic()
    clock.advance(90)
    clock.advance(timedelta(minutes=1), hours=1)
    assert clock.now() == START + timedelta(hours=1, minutes=2, seconds=30)
    assert clock.monotonic() - before == 3750.0


def test_a_start_is_kept_in_utc_and_none_is_now_to_the_second() -> None:
    assert ManualClock(datetime(2026, 10, 24, 12)).now() == START
    paris = ManualClock(datetime(2026, 10, 24, 14, tzinfo=timezone(timedelta(hours=2)))).now()
    assert (paris, paris.tzinfo) == (START, UTC)
    now = ManualClock().now()
    assert now.tzinfo is UTC
    assert now.microsecond == 0


def test_a_sleep_moves_the_clock_and_never_waits() -> None:
    clock = ManualClock(START)
    clock.sleep(3600)
    assert clock.slept == [3600]
    assert clock.now() == START + timedelta(hours=1)


@pytest.mark.parametrize("move", [lambda c: c.advance(-1), lambda c: c.sleep(-1)])
def test_the_clock_never_goes_back(move: object) -> None:
    clock = ManualClock(START)
    with pytest.raises(ValueError, match="never goes back"):
        move(clock)  # type: ignore[operator]
    assert clock.now() == START


def test_a_deadline_counts_down_on_the_clock() -> None:
    with manual_clock(START) as clock, request_deadline(10):
        clock.sleep(4)
        assert _time_left() == 6


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


def _resolving(clock: ManualClock, move: str) -> App:
    app = App(name="resolve")

    @app.default
    def resolve() -> None:
        with Diagnostics().progress("Resolving deps") as task:
            task.update("pinning", phase="installing")
            getattr(clock, move)(2)

    return app


@pytest.mark.parametrize("move", ["advance", "sleep"])
def test_progress_follows_the_clock_even_under_invoke_cli(move: str) -> None:
    with manual_clock(START) as clock:
        result = invoke_cli(_resolving(clock, move), [])
    assert result.exit_code == 0, result.stderr
    assert "Resolving deps · installing · pinning (2s)" in result.stderr


def test_a_deadline_keeps_the_clock_it_was_set_on() -> None:
    clock = ManualClock(START)
    with installed_clock(clock), request_deadline(10):
        clock.advance(4)
        with progress_clock() as progress:
            progress.advance(100)
            assert _time_left() == 6
            with request_deadline(20):
                assert _time_left() == 6
            with request_deadline(1):
                assert _time_left() == 1


def _waiting(left: list[float | None]) -> App:
    app = App(name="wait")

    @app.default
    def wait() -> None:
        with request_deadline(0.05):
            time.sleep(0.1)
            left.append(_time_left())

    return app


def test_a_deadline_runs_out_under_invoke_cli_while_progress_stands_still() -> None:
    left: list[float | None] = []
    assert invoke_cli(_waiting(left), []).exit_code == 0
    [after] = left
    assert after is not None and after < 0
