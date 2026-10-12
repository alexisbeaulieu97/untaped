"""``Cache.serve``: the directive, the fill, the sidecar and lock, and what it says on stderr.

A toy command serves the ``toy.items`` cache over a fake upstream; the wall
clock is the test's (``cache_clock``), and a test that wants progress lines
moves the progress clock from inside the fill.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import pytest

from untaped import cache as cache_module
from untaped.cache import (
    RETRY_AFTER,
    Cache,
    Filled,
    Freshness,
    FreshnessRecord,
    NeverRefreshed,
    call_state,
    refresh_option,
    refresh_scope,
)
from untaped.cli import create_app, echo, report_errors
from untaped.errors import ConfigError, ErrorCategory, HttpTransportError, UntapedError
from untaped.output.diagnostic import Failure, Progress, StaleData
from untaped.output.progress import ProgressHandle
from untaped.output.sink import (
    count_row_failures,
    diagnostics_scope,
    exit_on_counted_failure,
    open_tasks,
    recording,
)
from untaped.testing import CacheClock, CliResult, cache_clock, invoke_cli, progress_clock

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


def _sidecar(key: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(TOY._paths(key).sidecar.read_text(encoding="utf-8"))
    return data


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


def test_the_sidecar_holds_the_freshness_under_the_profile(clock: CacheClock) -> None:
    assert _show("a").exit_code == 0
    paths = TOY._paths("a")
    assert paths.sidecar.parent.name == "default"
    assert paths.sidecar.parent.parent.name == "items"
    assert _sidecar("a") == {
        "untaped": "1",
        "key": "a",
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
    from untaped.upstream import capped

    seen: list[bool] = []
    UP.during = lambda _key, _task: seen.append(capped())
    _show("a")  # a first fill keeps its full retries: there is no data to fall back to
    clock.advance(timedelta(hours=2))
    _show("a")
    clock.advance(timedelta(seconds=1))
    _show("a", "--refresh")
    assert seen == [False, True, False]


# ---- progress ------------------------------------------------------------------


@dataclass
class _Parent:
    phases: list[str | None] = field(default_factory=list)

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        self.phases.append(phase)


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
    assert parent.phases == ["Refreshing toy a (first use)", "waiting for another untaped process"]
    assert UP.fills == ["a"]


def test_a_waiter_serves_what_the_other_process_refreshed(clock: CacheClock) -> None:
    """Under --refresh too: the data was refreshed after this run started."""

    def other_process_fills() -> None:
        TOY._write(TOY._paths("a"), "a", Freshness(refreshed_at=START + timedelta(seconds=1)))
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
    assert not TOY._paths("a").sidecar.exists()


def test_a_fill_never_serves_its_own_cache(clock: CacheClock) -> None:
    def serve_again(_key: str, _task: ProgressHandle) -> None:
        TOY.serve({"a": "toy a"}, refresh=True, fill=UP.fill, read=lambda: [])

    UP.during = serve_again
    result = _show("a")

    assert result.exit_code == 1
    assert "a fill never serves its own cache" in result.stderr
    assert UP.fills == ["a"]


# ---- Ctrl-C ------------------------------------------------------------------------


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
    sidecars = {TOY._paths(key).sidecar for key in keys}
    assert len(sidecars) == 3
    assert all(path.exists() for path in sidecars)
    assert TOY._paths("a_b").sidecar.name == "a_b.json"
    assert TOY._paths("a/b").sidecar.name.startswith("a_b~")


def test_a_sidecar_for_another_key_reads_as_never_refreshed(clock: CacheClock) -> None:
    assert _show("a").exit_code == 0
    paths = TOY._paths("a")
    paths.sidecar.write_text(paths.sidecar.read_text().replace('"key": "a"', '"key": "z"'))
    assert TOY.freshness("a") == Freshness()


def test_a_failed_sidecar_write_keeps_the_old_one_and_the_answer(
    clock: CacheClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refreshed(clock, "a", ago=timedelta(hours=2))

    def full_disk(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cache_module, "atomic_write", full_disk)
    result = _show("a")

    assert (result.exit_code, UP.fills) == (0, ["a"])
    assert _sidecar("a")["refreshed_at"] == "2026-10-12T07:00:00Z"


def test_each_profile_has_its_own_sidecar(
    clock: CacheClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _show("a").exit_code == 0
    monkeypatch.setenv("UNTAPED_PROFILE", "work")

    assert _show("a", "--cached").exit_code == 1
    assert _show("a").exit_code == 0
    assert UP.fills == ["a", "a"]
    assert TOY._paths("a").sidecar.parent.name == "work"


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
    assert _show("a").exit_code == 0
    assert UP.fills == ["a"]


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
    clock: CacheClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight parallel first probes, one down: the fill completes, holed, with its retry time."""
    import httpx
    import respx

    from untaped.concurrency import bounded_map
    from untaped.http import HttpClient, RetryPolicy

    monkeypatch.setattr("untaped.http._sleep", lambda _seconds: None)
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

            bounded_map(
                probe,
                range(8),
                concurrency=8,
                on_each=lambda _repo, name: failed.extend([name] if name else []),
            )
        error = UntapedError(f"{len(failed)} of 8 repos failed to refresh ({', '.join(failed)})")
        return Filled(partial=Failure.from_exception(error), retry_at=reset, summary="8 repos")

    with respx.mock(base_url="https://example.com") as mock:
        mock.get("/repos/3").mock(side_effect=httpx.ConnectError("offline"))
        mock.get(url__regex=r"/repos/\d+").mock(return_value=httpx.Response(200))
        with diagnostics_scope():
            served = TOY.serve({"a": "toy a"}, refresh=None, fill=probe_all, read=lambda: ["x"])

    assert served.filled["a"].partial is not None
    assert served.filled["a"].partial.message == "1 of 8 repos failed to refresh (repo3)"
    sidecar = _sidecar("a")
    assert (sidecar["refreshed_at"], sidecar["partial"], sidecar["retry_at"]) == (
        "2026-10-12T09:00:00Z",
        True,
        "2026-10-12T09:30:00Z",
    )
