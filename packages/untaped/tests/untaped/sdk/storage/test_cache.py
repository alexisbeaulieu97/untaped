"""``Cache.serve``: the directive, the fill, the sidecar and lock, and what it says on stderr.

A toy command serves the ``toy.items`` cache over a fake upstream; the wall
clock is the test's (``cache_clock``), and a test that wants progress lines
moves the progress clock from inside the fill.
"""

from __future__ import annotations

import importlib.metadata
import json
import stat
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import pytest

from untaped.runtime.sink import (
    count_row_failures,
    diagnostics_scope,
    exit_on_counted_failure,
    open_tasks,
    recording,
)
from untaped.sdk._cli import create_app, echo, report_errors
from untaped.sdk.diagnostic import Failure, Progress, StaleData
from untaped.sdk.errors import ConfigError, ErrorCategory, HttpTransportError, UntapedError
from untaped.sdk.network.upstream import PROBE_TIMEOUT
from untaped.sdk.progress import ProgressHandle
from untaped.sdk.storage import cache as cache_module
from untaped.sdk.storage import data_file
from untaped.sdk.storage._digest import settings_digest
from untaped.sdk.storage.cache import (
    RETRY_AFTER,
    Cache,
    Filled,
    Freshness,
    FreshnessRecord,
    NeverRefreshed,
    call_state,
    code_version,
    refresh_option,
    refresh_scope,
)
from untaped.sdk.storage.data_file import written_by
from untaped.sdk.testing import CacheClock, CliResult, cache_clock, invoke_cli, progress_clock

TOY = Cache("toy", "items", label="toy items", max_age=timedelta(hours=1))
START = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


@dataclass
class Upstream:
    """The fake upstream and the owner's store the fill copies it into."""

    items: dict[str, list[str]] = field(default_factory=lambda: {"a": ["a1"], "b": ["b1"], "e": []})
    held: dict[str, list[str]] = field(default_factory=dict)
    error: BaseException | None = None
    partial: Failure | None = None
    retry_at: datetime | None = None
    fills: list[str] = field(default_factory=list)
    reads: int = 0
    during: Callable[[str, ProgressHandle], None] | None = None

    def fill(self, key: str, task: ProgressHandle) -> Filled:
        self.fills.append(key)
        if self.during is not None:
            self.during(key, task)
        if self.error is not None:
            raise self.error
        self.held[key] = list(self.items[key])
        return Filled(partial=self.partial, retry_at=self.retry_at, summary=f"{key}: 1 item")

    def read(self, keys: list[str]) -> list[str]:
        self.reads += 1
        return sorted(item for key in keys for item in self.held.get(key, []))


UP = Upstream()

app = create_app(name="toy", help="A toy cache.")


@app.command(name="show")
def show(keys: list[str], *, refresh: Annotated[bool | None, refresh_option(TOY)] = None) -> None:
    """Print the items of KEYS, served through the toy cache."""
    with report_errors():
        served = TOY.serve(
            {key: f"toy {key}" for key in keys},
            refresh=refresh,
            fill=UP.fill,
            read=lambda: UP.read(keys),
            empty=lambda value: not value,
        )
    echo(json.dumps(served.value))


@pytest.fixture(autouse=True)
def _upstream() -> Iterator[None]:
    global UP
    UP = Upstream()
    yield


@pytest.fixture
def clock() -> Iterator[CacheClock]:
    with cache_clock(START) as wall:
        yield wall


def _show(*args: str) -> CliResult:
    return invoke_cli(app, ["show", *args])


def _stale(result: CliResult) -> list[StaleData]:
    return [each for each in result.diagnostics if isinstance(each, StaleData)]


def _refreshed(clock: CacheClock, *keys: str, ago: timedelta) -> None:
    """Fill ``keys`` as of ``ago`` before now."""
    now = clock.now
    clock.advance(-ago)
    assert _show(*keys, "--refresh").exit_code == 0
    clock.advance(now - clock.now)
    UP.fills.clear()


def _sidecar(key: str, cache: Cache = TOY) -> dict[str, Any]:
    """The stamp on the key's file's first line."""
    line = cache._paths(key).file().read_text(encoding="utf-8").splitlines()[0]
    stamp: dict[str, Any] = json.loads(line)["untaped"]
    return stamp


def _write_sidecar(key: str, stamp: dict[str, Any], cache: Cache = TOY) -> None:
    file = cache._paths(key).file()
    file.write_text(json.dumps({"untaped": stamp}) + "\n", encoding="utf-8")


# ---- Freshness.due -----------------------------------------------------------

_HOUR = timedelta(hours=1)


@pytest.mark.parametrize(
    ("refresh", "age", "retry_in", "due"),
    [
        (False, None, None, False),
        (False, timedelta(hours=2), None, False),
        (True, timedelta(minutes=1), None, True),
        (True, timedelta(hours=2), timedelta(minutes=3), True),
        (None, None, None, True),
        (None, timedelta(minutes=59), None, False),
        (None, timedelta(hours=1), None, True),
        (None, timedelta(hours=2), timedelta(minutes=3), False),
        (None, timedelta(hours=2), timedelta(minutes=-1), True),
        (None, None, timedelta(minutes=3), True),  # never refreshed: nothing to fall back to
    ],
)
def test_due_is_the_directive_then_the_age_then_retry_at(
    refresh: bool | None, age: timedelta | None, retry_in: timedelta | None, due: bool
) -> None:
    state = Freshness(
        refreshed_at=None if age is None else START - age,
        retry_at=None if retry_in is None else START + retry_in,
    )
    assert state.due(refresh, max_age=_HOUR, now=START) is due


def test_a_zero_max_age_checks_every_time() -> None:
    assert Freshness(refreshed_at=START).due(None, max_age=timedelta(0), now=START)


# ---- the declaration -----------------------------------------------------------


def test_the_declaration_names_its_setting_and_default() -> None:
    assert TOY.qualified == "toy.items"
    assert TOY.setting == "toy.caches.items.max_age_seconds"
    assert TOY.default_max_age_text == "1 h"
    assert TOY.max_age() == _HOUR
    assert Cache("toy", "items", label="toy items", max_age=_HOUR) == TOY
    assert len({TOY, Cache("toy", "items", label="toy items", max_age=_HOUR)}) == 1


@pytest.mark.parametrize(
    ("plugin", "name", "max_age"),
    [
        ("toy", "Items", _HOUR),
        ("toy", "a.b", _HOUR),
        ("Toy", "items", _HOUR),
        ("toy", "items", -_HOUR),
    ],
)
def test_a_bad_declaration_fails_at_once(plugin: str, name: str, max_age: timedelta) -> None:
    with pytest.raises(ConfigError):
        Cache(plugin, name, label="x", max_age=max_age)


def test_the_option_names_the_setting_and_refuses_both_flags(clock: CacheClock) -> None:
    help_text = invoke_cli(app, ["show", "--help"]).stdout
    assert "toy.caches.items.max_age_seconds" in " ".join(help_text.split())
    assert "--cached" in help_text
    assert _show("a", "--refresh", "--cached").exit_code == 2


# ---- serve: fresh, stale, first use ------------------------------------------


def test_first_use_fills_then_a_fresh_read_is_silent(clock: CacheClock) -> None:
    first = _show("a")
    assert (first.exit_code, json.loads(first.stdout)) == (0, ["a1"])
    assert UP.fills == ["a"]
    clock.advance(timedelta(minutes=30))

    second = _show("a")
    assert (second.exit_code, json.loads(second.stdout)) == (0, ["a1"])
    assert UP.fills == ["a"]
    assert second.diagnostics == ()


def test_the_sidecar_holds_the_freshness_under_the_settings_digest(clock: CacheClock) -> None:
    assert _show("a").exit_code == 0
    paths = TOY._paths("a")
    assert paths.file().parent.name == settings_digest("toy")
    assert paths.file().parent.parent.name == "items"
    assert paths.file().parent.parent.parent.name == "cached"
    assert paths.file().read_text(encoding="utf-8").count("\n") == 1  # no value: one line
    assert stat.S_IMODE(paths.file().stat().st_mode) == 0o600
    assert _sidecar("a") == {
        "file": 1,
        "by": written_by(),
        "key": "a",
        "version": None,
        "refreshed_at": "2026-10-12T09:00:00Z",
        "attempted_at": None,
        "error": None,
        "partial": False,
        "retry_at": None,
    }


def test_a_stale_key_is_refreshed_under_a_task_that_says_its_age(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(hours=2))
    UP.items["a"] = ["a2"]
    with progress_clock() as ticks:
        UP.during = lambda _key, _task: ticks.advance(3)
        result = _show("a")

    assert json.loads(result.stdout) == ["a2"]
    lines = [each for each in result.diagnostics if isinstance(each, Progress)]
    assert {line.label for line in lines} == {"Refreshing toy a (2 h old, max age 1 h)"}
    assert (lines[-1].state, lines[-1].message) == (
        "done",
        "Refreshing toy a (2 h old, max age 1 h): done · a: 1 item (3s)",
    )
    assert _stale(result) == []


def test_two_keys_one_due_fill_once_and_read_once(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(minutes=10))
    _refreshed(clock, "b", ago=timedelta(hours=3))
    UP.reads = 0

    result = _show("a", "b")

    assert (result.exit_code, json.loads(result.stdout)) == (0, ["a1", "b1"])
    assert (UP.fills, UP.reads) == (["b"], 1)


# ---- serve: failures -----------------------------------------------------------


def test_a_failed_refresh_serves_stale_data_after_the_failed_task(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(hours=26))
    UP.error = HttpTransportError("connection refused", system="toy")
    with progress_clock() as ticks:
        UP.during = lambda _key, _task: ticks.advance(3)
        result = _show("a")

    assert (result.exit_code, json.loads(result.stdout)) == (0, ["a1"])
    kinds = [(type(each).__name__, getattr(each, "state", None)) for each in result.diagnostics]
    assert kinds == [("Progress", "running"), ("Progress", "failed"), ("StaleData", None)]
    [line] = _stale(result)
    assert (line.plugin, line.cache, line.key, line.partial) == ("toy", "toy.items", "a", False)
    assert line.error is not None and line.error.category is ErrorCategory.UNAVAILABLE
    assert line.retry_at == START + RETRY_AFTER
    assert line.message == (
        "toy a: refresh failed (connection refused); using data from 2026-10-11 07:00 UTC, "
        "26 h old; next check after 09:05 UTC"
    )
    assert line.hint == "retry later, or pass --cached to read it without checking"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (HttpTransportError("connection refused", system="toy"), 5),
        (ConfigError("token rejected", category="auth", system="toy"), 4),
    ],
)
def test_a_stale_empty_answer_fails_with_the_refresh_s_error(
    clock: CacheClock, error: UntapedError, code: int
) -> None:
    _refreshed(clock, "e", ago=timedelta(hours=2))
    UP.error = error

    result = _show("e")

    assert result.exit_code == code
    assert _stale(result) == []
    [failure] = [each for each in result.diagnostics if isinstance(each, Failure)]
    assert failure.message == str(error)
    assert failure.hint == "retry later, or pass --cached to read the cached data"
    assert failure.details["cache"] == "toy.items"
    assert failure.details["key"] == "e"
    assert failure.details["refreshed_at"] == "2026-10-12T07:00:00Z"


def test_cached_with_an_empty_answer_is_a_warning(clock: CacheClock) -> None:
    _refreshed(clock, "e", ago=timedelta(hours=2))
    result = _show("e", "--cached")

    assert (result.exit_code, json.loads(result.stdout), UP.fills) == (0, [], [])
    [line] = _stale(result)
    assert line.message == "toy e: cached data from 2026-10-12 07:00 UTC, 2 h old (--cached)"
    assert (line.error, line.attempted_at, line.retry_at) == (None, None, None)


def test_cached_after_a_failed_attempt_shows_it_and_never_raises_it(clock: CacheClock) -> None:
    _refreshed(clock, "e", ago=timedelta(hours=2))
    UP.error = HttpTransportError("connection refused", system="toy")
    assert _show("e").exit_code == 5
    clock.advance(timedelta(minutes=2))

    result = _show("e", "--cached")

    assert result.exit_code == 0
    [line] = _stale(result)
    assert line.message == (
        "toy e: refresh failed 2 min ago (connection refused); "
        "using data from 2026-10-12 07:00 UTC, 2 h old (--cached)"
    )
    assert line.retry_at == START + RETRY_AFTER


def test_never_refreshed_and_cached_never_reads(clock: CacheClock) -> None:
    result = _show("a", "--cached")

    assert result.exit_code == 1
    assert UP.reads == 0
    assert result.stderr.startswith(
        "error: no cached toy items for toy a; drop --cached to refresh it"
    )
    [failure] = result.diagnostics
    assert isinstance(failure, Failure)
    assert failure.details == {"cache": "toy.items", "key": "a"}


def test_never_refreshed_and_a_failed_fill_raises(clock: CacheClock) -> None:
    UP.error = HttpTransportError("connection refused", system="toy")
    result = _show("a")

    assert result.exit_code == 5
    assert UP.reads == 0
    assert _sidecar("a")["attempted_at"] == "2026-10-12T09:00:00Z"


def test_a_never_refreshed_key_fills_again_inside_its_retry_window(clock: CacheClock) -> None:
    """The retry window is a fallback: with nothing on disk, auto fills every time."""
    UP.error = HttpTransportError("connection refused", system="toy")
    assert _show("a").exit_code == 5
    clock.advance(timedelta(minutes=1))

    result = _show("a")

    assert result.exit_code == 5
    assert UP.fills == ["a", "a"]
    assert "connection refused" in result.stderr
    assert "drop --cached" not in result.stderr
    UP.error = None
    assert _show("a").exit_code == 0
    assert UP.fills == ["a", "a", "a"]


def test_attempt_memory_skips_the_fill_until_retry_at(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(hours=2))
    UP.error = HttpTransportError("connection refused", system="toy")
    assert _show("a").exit_code == 0
    clock.advance(timedelta(minutes=2))

    result = _show("a")

    assert UP.fills == ["a"]
    [line] = _stale(result)
    assert line.message.endswith("next check after 09:05 UTC, or pass --refresh")
    assert line.hint is None
    clock.advance(timedelta(minutes=4))
    _show("a")
    assert UP.fills == ["a", "a"]


def test_retry_at_comes_from_the_error_s_details(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(hours=2))
    UP.error = UntapedError(
        "rate limited", category="unavailable", details={"retry_at": "2026-10-12T10:30:00Z"}
    )
    [line] = _stale(_show("a"))

    assert line.retry_at == datetime(2026, 10, 12, 10, 30, tzinfo=UTC)
    assert line.message.endswith("next check after 10:30 UTC")


def test_an_unparsable_retry_at_falls_back_to_retry_after(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(hours=2))
    UP.error = UntapedError("x", category="unavailable", details={"retry_at": "soon"})
    _show("a")
    assert _sidecar("a")["retry_at"] == "2026-10-12T09:05:00Z"


def test_a_forced_refresh_that_fails_on_fresh_data_warns(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(minutes=10))
    UP.error = HttpTransportError("connection refused", system="toy")

    result = _show("a", "--refresh")

    assert (result.exit_code, json.loads(result.stdout)) == (0, ["a1"])
    [line] = _stale(result)
    assert line.message.startswith("toy a: refresh failed (connection refused)")


# ---- serve: partial fills ------------------------------------------------------


def test_a_partial_fill_warns_on_every_read_until_a_whole_refresh(clock: CacheClock) -> None:
    UP.partial = Failure.from_exception(UntapedError("1 of 2 items failed to refresh"))
    UP.retry_at = START + timedelta(hours=2)
    assert _show("a").exit_code == 0
    assert _sidecar("a")["partial"] is True
    assert _sidecar("a")["retry_at"] == "2026-10-12T11:00:00Z"
    clock.advance(timedelta(minutes=10))
    UP.partial = UP.retry_at = None

    result = _show("a")

    assert UP.fills == ["a"]  # auto: not due before its max age
    [line] = _stale(result)
    assert line.partial is True
    assert line.message.startswith("toy a: 1 of 2 items failed to refresh; using data from")
    assert line.hint == "pass --refresh to retry them"
    whole = _show("a", "--refresh")
    assert UP.fills == ["a", "a"]
    assert _stale(whole) == []
    assert _stale(_show("a")) == []


def test_an_empty_answer_from_a_partial_fill_fails(clock: CacheClock) -> None:
    UP.partial = Failure.from_exception(UntapedError("1 of 2 items failed", category="unavailable"))
    result = _show("e")
    assert result.exit_code == 5
    [failure] = [each for each in result.diagnostics if isinstance(each, Failure)]
    assert failure.hint == "pass --refresh to retry them"


# ---- serve: the directive ------------------------------------------------------


def test_refresh_none_reads_the_directive_gather_opened(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(minutes=1))
    with call_state(True):
        assert refresh_scope() is True
        with diagnostics_scope():
            TOY.serve({"a": "toy a"}, refresh=None, fill=UP.fill, read=lambda: UP.read(["a"]))
    assert UP.fills == ["a"]
    assert refresh_scope() is None


def test_only_an_auto_refill_runs_under_the_fail_fast_budget(clock: CacheClock) -> None:
    from untaped.sdk.network.upstream import capped

    seen: list[bool] = []
    UP.during = lambda _key, _task: seen.append(capped())
    _show("a")  # a first fill keeps its full retries: there is no data to fall back to
    clock.advance(timedelta(hours=2))
    _show("a")
    clock.advance(timedelta(seconds=1))
    _show("a", "--refresh")
    assert seen == [False, True, False]


@pytest.mark.usefixtures("clock")
def test_a_first_fill_retries_a_transient_failure() -> None:
    import httpx
    import respx

    from untaped.sdk.network.http import HttpClient, RetryPolicy

    def fetch(_key: str, _task: ProgressHandle) -> Filled:
        with HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client:
            client.get("/items")
        return Filled()

    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/items").mock(side_effect=[httpx.Response(503), httpx.Response(200)])
        with diagnostics_scope():
            TOY.serve({"a": "toy a"}, refresh=None, fill=fetch, read=lambda: ["x"])
    assert route.call_count == 2


@pytest.mark.parametrize("inner_refresh", [True, None])
def test_a_nested_forced_or_first_fill_leaves_the_outer_budget(
    clock: CacheClock, inner_refresh: bool | None
) -> None:
    """Under an outer automatic refresh, an inner --refresh or first fill keeps full retries."""
    from untaped.sdk.network.upstream import capped

    inner = Cache("toy", "inner", label="toy inner", max_age=_HOUR)
    seen: list[bool] = []

    def inner_fill(_key: str, _task: ProgressHandle) -> Filled:
        seen.append(capped())
        return Filled()

    def outer_fill(key: str, task: ProgressHandle) -> Filled:
        seen.append(capped())
        inner.serve({"i": "toy i"}, refresh=inner_refresh, fill=inner_fill, read=lambda: [])
        seen.append(capped())  # the outer budget is back
        return UP.fill(key, task)

    _refreshed(clock, "a", ago=timedelta(hours=2))
    with diagnostics_scope():
        TOY.serve({"a": "toy a"}, refresh=None, fill=outer_fill, read=lambda: UP.read(["a"]))
    assert seen == [True, False, True]


# ---- progress ------------------------------------------------------------------


@dataclass
class _Parent:
    phases: list[str | None] = field(default_factory=list)
    messages: list[str | None] = field(default_factory=list)

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        self.phases.append(phase)
        self.messages.append(message)


def test_under_a_parent_handle_the_refresh_is_a_phase_and_opens_no_task(
    clock: CacheClock,
) -> None:
    parent = _Parent()
    opened: list[int] = []
    UP.during = lambda _key, _task: opened.append(open_tasks())
    with diagnostics_scope():
        TOY.serve(
            {"a": "toy a"}, refresh=None, fill=UP.fill, read=lambda: UP.read(["a"]), progress=parent
        )
    assert opened == [0]
    assert parent.phases == ["Refreshing toy a (first use)"]


# ---- the lock ------------------------------------------------------------------


def _hold(key: str, *, seconds: float, then: Callable[[], None] = lambda: None) -> threading.Thread:
    """Hold ``key``'s lock from another thread for ``seconds``, run ``then``, release it."""
    taken = threading.Event()

    def hold() -> None:
        with TOY._held(TOY._paths(key), timeout=0.0, label=key):
            taken.set()
            threading.Event().wait(seconds)
            then()

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert taken.wait(5)
    return thread


def test_a_refresh_waits_for_the_lock_and_says_so(clock: CacheClock) -> None:
    parent = _Parent()
    holder = _hold("a", seconds=0.2)
    with diagnostics_scope():
        TOY.serve(
            {"a": "toy a"}, refresh=None, fill=UP.fill, read=lambda: UP.read(["a"]), progress=parent
        )
    holder.join(5)
    assert not holder.is_alive()
    assert parent.phases == [
        "Refreshing toy a (first use)",
        "waiting for another untaped process",
        "Refreshing toy a (first use)",  # back once the lock came
    ]
    assert UP.fills == ["a"]


def test_an_own_task_shows_the_wait_as_its_text_then_clears_it(clock: CacheClock) -> None:
    task = _Parent()
    holder = _hold("a", seconds=0.2)
    with TOY._lock(TOY._paths("a"), "toy a", task):
        assert task.messages == ["waiting for another untaped process", None]
        assert task.phases == [None, None]  # an own task's label is not a phase
    holder.join(5)


def test_a_waiter_serves_what_the_other_process_refreshed(clock: CacheClock) -> None:
    """Under --refresh too: the data was refreshed after this run started."""

    def other_process_fills() -> None:
        fresh = Freshness(refreshed_at=START + timedelta(seconds=1))
        TOY._write(TOY._paths("a").file(), "a", fresh, None, None)
        UP.held["a"] = ["a9"]

    for args in (["a"], ["a", "--refresh"]):
        holder = _hold("a", seconds=0.2, then=other_process_fills)
        result = _show(*args)
        holder.join(5)
        assert (result.exit_code, json.loads(result.stdout)) == (0, ["a9"])
        assert UP.fills == []


def test_a_lock_that_never_comes_exits_5(
    clock: CacheClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_module, "_LOCK_TIMEOUT", 0.1)
    holder = _hold("a", seconds=1.0)
    result = _show("a")
    holder.join(5)

    assert result.exit_code == 5
    assert "another untaped process is still refreshing toy a" in result.stderr
    assert UP.fills == []
    assert not TOY._paths("a").file().exists()


def test_a_fill_never_serves_its_own_cache(clock: CacheClock) -> None:
    def serve_again(_key: str, _task: ProgressHandle) -> None:
        TOY.serve({"a": "toy a"}, refresh=True, fill=UP.fill, read=lambda: [])

    UP.during = serve_again
    result = _show("a")

    assert result.exit_code == 1
    assert "a fill never serves its own cache" in result.stderr
    assert UP.fills == ["a"]


# ---- Ctrl-C ------------------------------------------------------------------------


def test_ctrl_c_on_a_first_fill_says_nothing_of_cached_data_and_the_rerun_fills(
    clock: CacheClock,
) -> None:
    UP.error = KeyboardInterrupt()
    result = _show("a")
    assert result.exit_code == 130
    assert result.stderr == "error: interrupted while refreshing toy a\n"

    clock.advance(timedelta(minutes=1))  # inside the retry window: no data to fall back to
    UP.error = None
    rerun = _show("a")
    assert (rerun.exit_code, rerun.diagnostics, UP.fills) == (0, (), ["a", "a"])


def test_ctrl_c_is_remembered_said_once_and_exits_130(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(hours=2))
    UP.error = KeyboardInterrupt()

    result = _show("a")

    assert result.exit_code == 130
    assert result.stderr == (
        "error: interrupted while refreshing toy a\n"
        "hint: pass --cached to read the cached data without checking\n"
    )
    [failure] = result.diagnostics
    assert isinstance(failure, Failure) and failure.category is ErrorCategory.INTERRUPTED
    sidecar = _sidecar("a")
    assert sidecar["refreshed_at"] == "2026-10-12T07:00:00Z"
    assert sidecar["attempted_at"] == "2026-10-12T09:00:00Z"
    assert sidecar["error"]["category"] == "interrupted"
    with TOY._held(TOY._paths("a"), timeout=0.0, label="a"):
        pass  # released

    clock.advance(timedelta(minutes=1))
    UP.error = None
    rerun = _show("a")
    assert (rerun.exit_code, UP.fills) == (0, ["a"])
    [line] = _stale(rerun)
    assert line.message.startswith("toy a: refresh interrupted 1 min ago; using data from")


# ---- files -------------------------------------------------------------------------


def test_a_url_key_and_keys_that_sanitize_alike_keep_their_own_sidecars(
    clock: CacheClock,
) -> None:
    keys = ["https://ghe.example/acme/x", "a/b", "a_b"]
    for key in keys:
        UP.items[key] = [key]
    assert _show(*keys).exit_code == 0
    sidecars = {TOY._paths(key).file() for key in keys}
    assert len(sidecars) == 3
    assert all(path.exists() for path in sidecars)
    assert TOY._paths("a_b").file().name == "a_b.json"
    assert TOY._paths("a/b").file().name.startswith("a_b~")


def test_a_long_key_and_keys_differing_in_case_keep_their_own_sidecars(
    clock: CacheClock,
) -> None:
    keys = ["https://ghe.example/" + "x" * 300, "日" * 90, "Abc", "abc"]
    for key in keys:
        UP.items[key] = [key]
    assert _show(*keys).exit_code == 0
    names = {TOY._paths(key).file().name.lower() for key in keys}
    assert len(names) == 4  # apart on a case-insensitive filesystem too
    assert all(len(name.encode()) < 120 for name in names)  # bytes: every filesystem's limit
    assert TOY._paths("abc").file().name == "abc.json"


def test_values_that_sanitize_alike_keep_their_own_segments() -> None:
    segments = {cache_module._segment(name).lower() for name in ("a/b", "a_b", "Prod", "prod")}
    assert len(segments) == 4


def test_a_complete_fill_sweeps_the_key_from_old_settings_folders(clock: CacheClock) -> None:
    _refreshed(clock, "a", "b", ago=timedelta(days=31))
    own = TOY._paths("a").dir
    old = own.parent / "0000000000000000"
    own.rename(old)
    _refreshed(clock, "a", ago=timedelta(days=1))
    recent = own.parent / "1111111111111111"
    own.rename(recent)

    assert _show("a", "--refresh").exit_code == 0

    assert [file.name for file in old.glob("*.json")] == ["b.json"]  # another key's: kept
    assert [file.name for file in recent.glob("*.json")] == ["a.json"]  # inside the horizon
    _show("b", "--refresh")
    assert not old.exists()  # its last file went, then its locks and the folder


def test_an_unreadable_error_keeps_the_data_and_drops_the_attempt(clock: CacheClock) -> None:
    """A newer untaped's error: the data stays, the attempt it describes cannot be told."""
    _refreshed(clock, "a", ago=timedelta(minutes=5))
    TOY._paths("a")
    data = _sidecar("a")
    data.update(
        error={"message": "x", "from_a_newer_untaped": True},
        attempted_at="2026-10-12T08:58:00Z",
        retry_at="2026-10-12T09:03:00Z",
        partial=True,
    )
    _write_sidecar("a", data)
    assert TOY.freshness("a") == Freshness(refreshed_at=START - timedelta(minutes=5))
    result = _show("a")
    assert (result.exit_code, result.diagnostics, UP.fills) == (0, (), [])


def test_a_sidecar_for_another_key_reads_as_never_refreshed(clock: CacheClock) -> None:
    assert _show("a").exit_code == 0
    TOY._paths("a")
    _write_sidecar("a", {**_sidecar("a"), "key": "z"})
    assert TOY.freshness("a") == Freshness()


def test_a_failed_sidecar_write_keeps_the_old_one_and_the_answer(
    clock: CacheClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refreshed(clock, "a", ago=timedelta(hours=2))

    def full_disk(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(data_file, "atomic_write", full_disk)
    result = _show("a")

    assert (result.exit_code, UP.fills) == (0, ["a"])
    assert result.stderr.endswith(
        "warning: could not keep toy items for toy a (No space left on device); "
        "the next run refreshes it again\n"
    )
    assert _sidecar("a")["refreshed_at"] == "2026-10-12T07:00:00Z"


def test_profiles_whose_settings_read_the_same_share_a_sidecar(
    clock: CacheClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _show("a").exit_code == 0
    monkeypatch.setenv("UNTAPED_PROFILE", "work")

    assert _show("a", "--cached").exit_code == 0
    assert UP.fills == ["a"]


# ---- the stale line, once ------------------------------------------------------


def test_two_serves_of_one_key_say_it_once_and_the_first_line_wins(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(hours=2))
    UP.error = HttpTransportError("connection refused", system="toy")
    with recording() as emitted, diagnostics_scope():
        TOY.serve({"a": "toy a"}, refresh=False, fill=UP.fill, read=lambda: [])
        TOY.serve({"a": "toy a"}, refresh=True, fill=UP.fill, read=lambda: [])
    [line] = [each for each in emitted if isinstance(each, StaleData)]
    assert line.message.endswith("(--cached)")


# ---- peeks, seed and forget ----------------------------------------------------------


def test_cached_peeks_without_a_lock_a_line_or_a_raise(clock: CacheClock) -> None:
    assert TOY.cached("a", lambda: UP.read(["a"])) is None
    _refreshed(clock, "a", ago=timedelta(hours=2))
    holder = _hold("a", seconds=0.5)
    with recording() as emitted, diagnostics_scope():
        served = TOY.cached("a", lambda: UP.read(["a"]))
    holder.join(5)
    assert served is not None
    assert (served.value, served.stale, dict(served.filled)) == (["a1"], True, {})
    assert served.freshness["a"].refreshed_at == START - timedelta(hours=2)
    assert emitted == []


def test_naive_times_are_utc() -> None:
    assert Filled(retry_at=datetime(2026, 10, 12, 9, 30)).retry_at == START + timedelta(minutes=30)
    with cache_clock(datetime(2026, 10, 12, 9, 0)) as wall:
        assert wall.now == START


def test_seed_writes_only_what_the_cache_does_not_know(clock: CacheClock) -> None:
    assert TOY.freshness("a") == Freshness()
    assert TOY.seed("a", refreshed_at=START - timedelta(minutes=5)) is True
    assert TOY.seed("a", refreshed_at=START) is False
    assert TOY.freshness("a").refreshed_at == START - timedelta(minutes=5)
    UP.held["a"] = ["a0"]

    assert json.loads(_show("a").stdout) == ["a0"]
    assert UP.fills == []


def test_seed_never_waits_for_a_fill(clock: CacheClock) -> None:
    holder = _hold("a", seconds=0.3)
    assert TOY.seed("a", refreshed_at=START) is False
    holder.join(5)


def test_forget_makes_the_next_read_refresh(clock: CacheClock) -> None:
    _refreshed(clock, "a", ago=timedelta(minutes=5))
    TOY.forget("a")
    assert TOY.freshness("a") == Freshness()
    TOY.forget("a")  # nothing to forget
    assert _show("a", "--cached").exit_code == 1  # NeverRefreshed
    assert UP.fills == []
    assert _show("a").exit_code == 0
    assert UP.fills == ["a"]


# ---- version= --------------------------------------------------------------------

#: The version of the code that fills ``VERSIONED``; a test sets it.
CODE = {"version": "1"}
VERSIONED = Cache(
    "toy", "versioned", label="toy items", max_age=_HOUR, version=lambda: CODE["version"]
)


@app.command(name="versioned")
def versioned(
    keys: list[str], *, refresh: Annotated[bool | None, refresh_option(VERSIONED)] = None
) -> None:
    """Print the items of KEYS, served through the versioned toy cache."""
    with report_errors():
        served = VERSIONED.serve(
            {key: f"toy {key}" for key in keys},
            refresh=refresh,
            fill=UP.fill,
            read=lambda: UP.read(keys),
        )
    echo(json.dumps(served.value))


@pytest.fixture
def code() -> Iterator[dict[str, str]]:
    CODE["version"] = "1"
    yield CODE
    CODE["version"] = "1"


def _versioned(*args: str) -> CliResult:
    return invoke_cli(app, ["versioned", *args])


def test_a_key_filled_at_another_version_is_due_and_keeps_its_data(
    clock: CacheClock, code: dict[str, str]
) -> None:
    assert _versioned("a").exit_code == 0
    assert _sidecar("a", VERSIONED)["version"] == "1"
    clock.advance(timedelta(minutes=5))
    assert _versioned("a").exit_code == 0
    assert UP.fills == ["a"]  # young and the same version: no fill

    code["version"] = "2"
    UP.items["a"] = ["a2"]
    with progress_clock() as ticks:
        UP.during = lambda _key, _task: ticks.advance(3)
        result = _versioned("a")

    assert (result.exit_code, json.loads(result.stdout), UP.fills) == (0, ["a2"], ["a", "a"])
    labels = {each.label for each in result.diagnostics if isinstance(each, Progress)}
    assert labels == {"Refreshing toy a (made by another version)"}
    assert _stale(result) == []
    assert VERSIONED.freshness("a") == Freshness(
        refreshed_at=START + timedelta(minutes=5), version="2"
    )


def test_a_failed_refresh_of_an_outdated_key_serves_it_and_waits_for_retry_at(
    clock: CacheClock, code: dict[str, str]
) -> None:
    assert _versioned("a").exit_code == 0
    clock.advance(timedelta(minutes=1))
    code["version"] = "2"
    UP.error = HttpTransportError("connection refused", system="toy")

    result = _versioned("a")

    assert (result.exit_code, json.loads(result.stdout)) == (0, ["a1"])
    [line] = _stale(result)
    assert line.message.startswith("toy a: refresh failed (connection refused)")
    assert line.outdated is True
    state = VERSIONED.freshness("a")
    assert (state.version, state.outdated) == ("1", True)
    assert state.retry_at == clock.now + RETRY_AFTER

    clock.advance(timedelta(minutes=2))
    _versioned("a")
    assert UP.fills == ["a", "a"]  # inside the retry window
    clock.advance(timedelta(minutes=4))
    UP.error = None
    _versioned("a")
    assert UP.fills == ["a", "a", "a"]
    assert VERSIONED.freshness("a").outdated is False


def test_cached_serves_an_outdated_key_with_one_stale_line(
    clock: CacheClock, code: dict[str, str]
) -> None:
    assert _versioned("a").exit_code == 0
    clock.advance(timedelta(minutes=10))
    code["version"] = "2"

    result = _versioned("a", "--cached")

    assert (result.exit_code, json.loads(result.stdout), UP.fills) == (0, ["a1"], ["a"])
    [line] = _stale(result)
    assert line.message == (
        "toy a: made by another version of the toy plugin; "
        "using data from 2026-10-12 09:00 UTC, 10 min old (--cached)"
    )
    assert (line.outdated, line.error) == (True, None)
    served = VERSIONED.cached("a", lambda: UP.read(["a"]))
    assert served is not None and served.stale is True


def test_a_seeded_key_is_outdated_under_a_versioned_cache(
    clock: CacheClock, code: dict[str, str]
) -> None:
    assert VERSIONED.seed("a", refreshed_at=START - timedelta(minutes=1)) is True
    seeded = VERSIONED.freshness("a")
    assert (seeded.version, seeded.outdated) == (None, True)

    assert _versioned("a").exit_code == 0
    assert UP.fills == ["a"]


def test_an_unversioned_cache_ignores_a_sidecar_version(clock: CacheClock) -> None:
    assert _show("a").exit_code == 0
    _write_sidecar("a", {**_sidecar("a"), "version": "9"})

    assert TOY.freshness("a") == Freshness(refreshed_at=START, version="9")
    assert _show("a").exit_code == 0
    assert UP.fills == ["a"]


def test_a_sidecar_without_a_version_reads_as_none(clock: CacheClock, code: dict[str, str]) -> None:
    assert _versioned("a").exit_code == 0
    raw = _sidecar("a", VERSIONED)
    del raw["version"]
    _write_sidecar("a", raw, VERSIONED)

    assert VERSIONED.freshness("a") == Freshness(refreshed_at=START, outdated=True)


def test_a_never_refreshed_key_is_not_outdated(clock: CacheClock, code: dict[str, str]) -> None:
    UP.error = HttpTransportError("connection refused", system="toy")
    assert _versioned("a").exit_code == 5  # the failed first fill leaves a sidecar

    state = VERSIONED.freshness("a")
    assert (state.refreshed_at, state.outdated) == (None, False)


def test_a_waiter_served_another_version_s_fill_says_so_without_cached(
    clock: CacheClock, code: dict[str, str]
) -> None:
    """Another install at another version filled the key while this run waited."""
    assert _versioned("a").exit_code == 0
    clock.advance(timedelta(minutes=1))
    code["version"] = "2"

    real_load = Cache._load
    loads: list[int] = []

    def load(self: Cache, paths: Any, key: str, version: str | None, typing: Any) -> Any:
        loads.append(1)
        if len(loads) == 2:  # the reload under the lock: the other install just filled it
            clock.advance(timedelta(seconds=1))
            fresh = Freshness(refreshed_at=clock.now, version="1")
            self._write(paths.file(), key, fresh, None, None)
        return real_load(self, paths, key, version, typing)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Cache, "_load", load)
        result = _versioned("a")

    assert (result.exit_code, UP.fills) == (0, ["a"])  # served the other fill, no fill of its own
    [line] = _stale(result)
    assert line.outdated is True
    assert line.message == (
        "toy a: made by another version of the toy plugin; "
        "using data from 2026-10-12 09:01 UTC, 0 s old"
    )


def test_the_version_is_read_once_per_serve(clock: CacheClock) -> None:
    calls: list[int] = []

    def version() -> str:
        calls.append(1)
        return "1"

    cache = Cache("toy", "counted", label="toy items", max_age=_HOUR, version=version)
    cache.serve({"a": "toy a", "b": "toy b"}, refresh=None, fill=UP.fill, read=lambda: None)

    assert (len(calls), UP.fills) == (1, ["a", "b"])
    assert cache == Cache("toy", "counted", label="toy items", max_age=_HOUR, version=version)
    assert cache != Cache("toy", "counted", label="toy items", max_age=_HOUR)


def test_an_outdated_key_s_status_row_is_stale() -> None:
    state = Freshness(refreshed_at=START, version="1", outdated=True)
    fields = FreshnessRecord.fields(state, max_age=_HOUR, now=START)
    assert (fields["stale"], fields["outdated"]) == (True, True)
    assert state.due(None, max_age=_HOUR, now=START)
    assert not state.due(False, max_age=_HOUR, now=START)


# ---- code_version ----------------------------------------------------------------


@pytest.fixture
def module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    package = tmp_path / "toy_code_version"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = package / "parser.py"
    source.write_text("RULES = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    cache_module._code_version.cache_clear()
    yield source
    cache_module._code_version.cache_clear()
    sys.modules.pop("toy_code_version", None)


def test_code_version_follows_the_module_s_bytes(module: Path) -> None:
    first = code_version("toy_code_version.parser")
    assert code_version("toy_code_version.parser") == first
    assert "toy_code_version.parser" not in sys.modules  # found, never imported

    module.write_text("RULES = 2\n")
    cache_module._code_version.cache_clear()
    assert code_version("toy_code_version.parser") != first


def test_code_version_follows_distributions_and_extra(
    module: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = {"PyYAML": "6.0.2"}

    def version(name: str) -> str:
        if name not in installed:
            raise importlib.metadata.PackageNotFoundError(name)
        return installed[name]

    monkeypatch.setattr(importlib.metadata, "version", version)
    base = code_version("toy_code_version.parser", distributions=("PyYAML",))
    cache_module._code_version.cache_clear()
    assert code_version("toy_code_version.parser", distributions=("PyYAML",)) == base

    installed["PyYAML"] = "6.0.3"
    cache_module._code_version.cache_clear()
    upgraded = code_version("toy_code_version.parser", distributions=("PyYAML",))
    assert upgraded != base

    absent = code_version("toy_code_version.parser", distributions=("nope",))
    plain = code_version("toy_code_version.parser")
    assert len({upgraded, absent, plain}) == 3
    assert code_version("toy_code_version.parser", extra=("libyaml",)) != plain


def test_code_version_refuses_a_module_without_a_file_and_a_bare_string(module: Path) -> None:
    with pytest.raises(ConfigError, match="no source file"):
        code_version("toy_code_version.missing")
    with pytest.raises(ConfigError, match="no source file"):
        code_version("no_such_package_here.parser")
    with pytest.raises(TypeError, match="not one string"):
        code_version("toy_code_version.parser", distributions="PyYAML")


# ---- the status row ------------------------------------------------------------


class _ToyStatus(FreshnessRecord, kind="toy.status"):
    key: str


def test_a_status_row_reports_a_remembered_error_without_failing(clock: CacheClock) -> None:
    error = Failure.from_exception(HttpTransportError("connection refused", system="toy"))
    state = Freshness(
        refreshed_at=START - timedelta(hours=2),
        attempted_at=START,
        error=error,
        retry_at=START + RETRY_AFTER,
    )
    row = _ToyStatus(key="a", **_ToyStatus.fields(state, max_age=_HOUR, now=START))

    assert row.model_dump(mode="json") == {
        "key": "a",
        "refreshed_at": "2026-10-12T07:00:00Z",
        "stale": True,
        "max_age_seconds": 3600,
        "partial": False,
        "outdated": False,
        "attempted_at": "2026-10-12T09:00:00Z",
        "retry_at": "2026-10-12T09:05:00Z",
        "refresh_error": error.model_dump(mode="json"),
    }
    with diagnostics_scope():
        count_row_failures([row])
        exit_on_counted_failure()  # no SystemExit: a remembered failure is not a failed row


def test_never_refreshed_is_an_sdk_error() -> None:
    error = NeverRefreshed(TOY, "a", "toy a")
    assert (error.category, error.exit_code, error.system) == (ErrorCategory.NOT_FOUND, 1, "toy")


def test_probes_that_fail_inside_the_budget_are_the_fill_s_partial_failures(
    clock: CacheClock,
) -> None:
    """An automatic refresh, the first probe down: the fill completes, holed, with its retry time.

    The down probe fails once, capped, inside the budget; the next succeeds
    and lifts the cap for the seven parallel ones.
    """
    import httpx
    import respx

    from untaped.sdk._concurrency import bounded_map
    from untaped.sdk.network.http import HttpClient, RetryPolicy

    _refreshed(clock, "a", ago=timedelta(hours=2))
    reset = START + timedelta(minutes=30)

    def probe_all(_key: str, _task: ProgressHandle) -> Filled:
        failed: list[str] = []
        with HttpClient(base_url="https://example.com", retry=RetryPolicy()) as client:

            def probe(repo: int) -> str | None:
                try:
                    client.get(f"/repos/{repo}")
                except HttpTransportError:
                    return f"repo{repo}"
                return None

            failed.extend([name] if (name := probe(3)) else [])
            bounded_map(
                probe,
                [0, 1, 2, 4, 5, 6, 7],
                concurrency=8,
                on_each=lambda _repo, name: failed.extend([name] if name else []),
            )
        error = UntapedError(f"{len(failed)} of 8 repos failed to refresh ({', '.join(failed)})")
        return Filled(partial=Failure.from_exception(error), retry_at=reset, summary="8 repos")

    with respx.mock(base_url="https://example.com") as mock:
        down = mock.get("/repos/3").mock(side_effect=httpx.ConnectError("offline"))
        mock.get(url__regex=r"/repos/\d+").mock(return_value=httpx.Response(200))
        with diagnostics_scope():
            served = TOY.serve({"a": "toy a"}, refresh=None, fill=probe_all, read=lambda: ["x"])

    assert down.call_count == 1  # capped and unretried: the budget was not yet lifted
    assert down.calls[0].request.extensions["timeout"]["read"] == PROBE_TIMEOUT

    assert served.filled["a"].partial is not None
    assert served.filled["a"].partial.message == "1 of 8 repos failed to refresh (repo3)"
    sidecar = _sidecar("a")
    assert (sidecar["refreshed_at"], sidecar["partial"], sidecar["retry_at"]) == (
        "2026-10-12T09:00:00Z",
        True,
        "2026-10-12T09:30:00Z",
    )


def test_a_key_named_like_a_version_is_found_by_its_stamp(clock: CacheClock) -> None:
    UP.items["x.vdeadbeef"] = ["x1"]
    assert _show("x.vdeadbeef").exit_code == 0
    folder = TOY._paths("x.vdeadbeef").dir
    [(entry, stem, refreshed)] = TOY._key_files(
        folder, "x.vdeadbeef", cache_module._VERSIONED, cache_module._PLAIN
    )
    assert (entry.name, stem, refreshed) == ("x.vdeadbeef.json", "x.vdeadbeef", START)
    assert list(TOY._key_files(folder, "x", cache_module._VERSIONED, cache_module._PLAIN)) == []
