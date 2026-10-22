"""``Cache(model=)``: each key's value and freshness in one file per version of the value."""

from __future__ import annotations

import errno
import json
import stat
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock
from pydantic import BaseModel

from untaped import data_file, refs
from untaped.cache import Cache, Filled, Freshness, NeverRefreshed
from untaped.contracts import cached
from untaped.errors import ConfigError, HttpTransportError
from untaped.output.diagnostic import Diagnostic, Pruned, StaleData
from untaped.output.progress import ProgressHandle
from untaped.output.sink import diagnostics_scope, recording
from untaped.plugins.registry import named_plugin_dir
from untaped.settings_digest import settings_digest
from untaped.testing import CacheClock, cache_clock, damage, keep_value

START = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
TOKEN = "s3cr3t-token-value"


class Snapshot(BaseModel):
    """What the controller holds."""

    items: list[str]
    online: bool = True


CODE = {"version": "1"}
SNAPSHOTS = Cache(
    "toy",
    "snapshots",
    label="controller resources",
    max_age=timedelta(hours=1),
    version=lambda: CODE["version"],
    model=Snapshot,
)


@dataclass
class Controller:
    items: dict[str, list[str]] = field(default_factory=lambda: {"https://c.example": ["jt"]})
    error: Exception | None = None
    fills: list[str] = field(default_factory=list)

    def fill(self, key: str, _task: ProgressHandle) -> Filled[Snapshot]:
        self.fills.append(key)
        if self.error is not None:
            raise self.error
        return Filled(value=Snapshot(items=self.items[key]), summary="1 resource")  # as documented


@pytest.fixture
def clock() -> Iterator[CacheClock]:
    CODE["version"] = "1"
    with cache_clock(START) as wall:
        yield wall


@pytest.fixture
def up() -> Controller:
    return Controller()


KEY = "https://c.example"


def _serve(
    up: Controller,
    *,
    refresh: bool | None = None,
    cache: Cache[Snapshot] = SNAPSHOTS,
    partition: str | None = None,
) -> tuple[Any, list[Diagnostic]]:
    with recording() as emitted, diagnostics_scope():
        served = cache.serve({KEY: "c.example"}, refresh=refresh, fill=up.fill, partition=partition)
    return served, list(emitted)


def _stale(emitted: list[Diagnostic]) -> list[StaleData]:
    return [each for each in emitted if isinstance(each, StaleData)]


def _files(cache: Cache[Any] = SNAPSHOTS) -> list[Path]:
    return sorted(cache._folder().glob("*.json"))


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# --- what a fill keeps -----------------------------------------------------------------


def test_a_fill_keeps_the_stamp_then_the_value_in_one_owner_only_file(
    clock: CacheClock, up: Controller
) -> None:
    served, _ = _serve(up)

    assert served.value == {KEY: Snapshot(items=["jt"])}
    [file] = _files()
    assert file.parent.parent == named_plugin_dir("toy") / "cached" / "snapshots"
    assert file.name.startswith("https___c.example~") and ".v" in file.name
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    head, value = _lines(file)
    stamp = json.loads(head)["untaped"]
    assert (stamp["key"], stamp["version"], stamp["refreshed_at"]) == (
        KEY,
        "1",
        "2026-10-21T09:00:00Z",
    )
    assert len(stamp["type"]) == 16
    assert json.loads(value) == {"items": ["jt"], "online": True}


def test_a_young_key_is_served_from_its_file(clock: CacheClock, up: Controller) -> None:
    _serve(up)
    clock.advance(timedelta(minutes=10))
    up.items[KEY] = ["changed"]
    served, emitted = _serve(up)
    assert served.value[KEY] == Snapshot(items=["jt"])
    assert (up.fills, _stale(emitted)) == ([KEY], [])


def test_a_failed_refresh_rewrites_the_first_line_and_keeps_the_values_bytes(
    clock: CacheClock, up: Controller
) -> None:
    _serve(up)
    [file] = _files()
    value = _lines(file)[1]
    clock.advance(timedelta(hours=2))
    up.error = HttpTransportError("connection refused", system="toy")

    served, emitted = _serve(up)

    assert served.value[KEY] == Snapshot(items=["jt"])
    [line] = _stale(emitted)
    assert line.message.startswith("c.example: refresh failed (connection refused)")
    head, kept = _lines(file)
    assert kept == value
    stamp = json.loads(head)["untaped"]
    assert stamp["attempted_at"] == "2026-10-21T11:00:00Z"
    assert stamp["refreshed_at"] == "2026-10-21T09:00:00Z"


def test_a_damaged_file_is_missing_and_refilled(clock: CacheClock, up: Controller) -> None:
    _serve(up)
    damage(SNAPSHOTS, KEY)
    kept = SNAPSHOTS._load(SNAPSHOTS._paths(KEY), KEY, "1", SNAPSHOTS._typing())
    assert (kept.freshness, kept.damaged) == (Freshness(), True)
    assert SNAPSHOTS._task_text("c.example", kept, timedelta(hours=1)) == (
        "Refreshing c.example (the kept copy could not be read)"
    )
    served, _ = _serve(up)
    assert served.value[KEY] == Snapshot(items=["jt"])
    assert up.fills == [KEY, KEY]


def test_a_value_that_no_longer_validates_is_refilled(clock: CacheClock, up: Controller) -> None:
    _serve(up)
    [file] = _files()
    head, _ = _lines(file)
    file.write_text(head + '\n{"items": "not a list"}\n', encoding="utf-8")
    assert SNAPSHOTS.freshness(KEY).refreshed_at == START  # the first line alone says fresh
    served, _ = _serve(up)
    assert served.value[KEY] == Snapshot(items=["jt"]) and up.fills == [KEY, KEY]


# --- other versions ----------------------------------------------------------------


def test_another_versions_value_is_outdated_served_under_cached(
    clock: CacheClock, up: Controller
) -> None:
    _serve(up)
    clock.advance(timedelta(minutes=10))
    CODE["version"] = "2"

    state = SNAPSHOTS.freshness(KEY)
    assert (state.version, state.outdated, state.refreshed_at) == ("1", True, START)
    served, emitted = _serve(up, refresh=False)

    assert served.value[KEY] == Snapshot(items=["jt"]) and up.fills == [KEY]
    [line] = _stale(emitted)
    assert line.outdated is True


def test_another_versions_value_stands_in_for_a_failed_refresh(
    clock: CacheClock, up: Controller
) -> None:
    _serve(up)
    clock.advance(timedelta(minutes=1))
    CODE["version"] = "2"
    up.error = HttpTransportError("connection refused", system="toy")

    served, emitted = _serve(up)

    assert served.value[KEY] == Snapshot(items=["jt"])
    assert [line.outdated for line in _stale(emitted)] == [True]
    assert len(_files()) == 2  # this version's file now holds the fallback, outdated
    state = SNAPSHOTS.freshness(KEY)
    assert (state.version, state.outdated, state.attempted_at) == ("1", True, clock.now)

    up.error = None
    clock.advance(timedelta(minutes=6))
    served, _ = _serve(up)
    assert SNAPSHOTS.freshness(KEY) == Freshness(refreshed_at=clock.now, version="2")


def test_another_types_file_is_missing(clock: CacheClock, up: Controller) -> None:
    _serve(up)
    [file] = _files()
    head, value = _lines(file)
    data = json.loads(head)
    data["untaped"]["type"] = "0" * 16
    file.write_text(json.dumps(data) + "\n" + value + "\n", encoding="utf-8")
    with pytest.raises(NeverRefreshed):
        _serve(up, refresh=False)
    assert SNAPSHOTS.freshness(KEY) == Freshness()


def test_refresh_fills_its_own_version_when_another_filled_meanwhile(
    clock: CacheClock, up: Controller
) -> None:
    """Two installs share the folder: another version's fill is no answer to --refresh."""
    later = START + timedelta(seconds=1)
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["other"]), refreshed_at=later, version="0")
    served, emitted = _serve(up, refresh=True)
    assert (up.fills, served.value[KEY], _stale(emitted)) == ([KEY], Snapshot(items=["jt"]), [])


def test_a_versions_failed_first_fill_still_falls_back_on_another_versions_value(
    clock: CacheClock, up: Controller
) -> None:
    up.error = HttpTransportError("connection refused", system="toy")
    with pytest.raises(HttpTransportError):
        _serve(up)
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["other"]), refreshed_at=START, version="0")

    served, _ = _serve(up, refresh=False)

    assert served.value[KEY] == Snapshot(items=["other"])
    state = SNAPSHOTS.freshness(KEY)
    assert (state.version, state.outdated, state.attempted_at) == ("0", True, START)
    assert state.error is not None  # this version's own attempt is still told


def test_the_newest_other_version_that_validates_is_the_fallback(
    clock: CacheClock, up: Controller
) -> None:
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["older"]), refreshed_at=START, version="0")
    clock.advance(timedelta(minutes=1))
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["newer"]), refreshed_at=clock.now, version="2")
    [newer] = [file for file in _files() if '"version":"2"' in _lines(file)[0]]
    newer.write_text(_lines(newer)[0] + '\n{"items": "broken"}\n', encoding="utf-8")

    served, _ = _serve(up, refresh=False)

    assert served.value[KEY] == Snapshot(items=["older"])


def test_a_fill_that_could_not_be_kept_leaves_the_older_files(
    clock: CacheClock, up: Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["old"]), refreshed_at=START, version="0")
    clock.advance(timedelta(hours=2))

    def full(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(data_file, "atomic_write", full)
    served, _ = _serve(up)

    assert served.value[KEY] == Snapshot(items=["jt"])
    monkeypatch.undo()
    assert [json.loads(_lines(file)[0])["untaped"]["version"] for file in _files()] == ["0"]


def test_a_complete_fill_sweeps_other_versions_past_their_max_age(
    clock: CacheClock, up: Controller
) -> None:
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["old"]), refreshed_at=START, version="0")
    keep_value(
        SNAPSHOTS, KEY, Snapshot(items=["young"]), refreshed_at=START, version="other install"
    )
    keep_value(SNAPSHOTS, "another key", Snapshot(items=["x"]), refreshed_at=START, version="0")
    clock.advance(timedelta(minutes=30))
    keep_value(
        SNAPSHOTS,
        KEY,
        Snapshot(items=["young"]),
        refreshed_at=clock.now,
        version="other install",
    )
    clock.advance(timedelta(minutes=40))  # "0" is 70 min old, "other install" 40 min

    _serve(up)

    versions = sorted(json.loads(_lines(file)[0])["untaped"]["version"] for file in _files())
    assert versions == ["0", "1", "other install"]  # another key's file is its own business
    assert {json.loads(_lines(file)[0])["untaped"]["key"] for file in _files()} == {
        KEY,
        "another key",
    }


def test_another_partitions_files_go_only_past_the_long_horizon(
    clock: CacheClock, up: Controller
) -> None:
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["a"]), refreshed_at=START, partition="old token")
    keep_value(
        SNAPSHOTS, KEY, Snapshot(items=["b"]), refreshed_at=START, partition="rotated", version="0"
    )
    clock.advance(timedelta(days=29))
    _serve(up, partition=TOKEN)
    assert len(_files()) == 3

    clock.advance(timedelta(days=2))
    _serve(up, refresh=True, partition=TOKEN)
    [file] = _files()
    assert json.loads(_lines(file)[1])["items"] == ["jt"]


def _sibling(name: str, *keys: str) -> Path:
    """Move ``keys``' files (every partition and version) to sibling settings folder ``name``."""
    own = SNAPSHOTS._folder()
    sibling = own.parent / name
    sibling.mkdir()
    for key in keys:
        stem = SNAPSHOTS._paths(key).stem
        for file in own.glob(f"{stem}*"):
            file.rename(sibling / file.name)
    return sibling


def test_a_fill_sweeps_the_key_from_sibling_settings_folders_past_the_horizon(
    clock: CacheClock, up: Controller
) -> None:
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["old"]), refreshed_at=START, partition="t")
    old = _sibling("0000000000000000", KEY)
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["old"]), refreshed_at=START)
    keep_value(SNAPSHOTS, "another key", Snapshot(items=["x"]), refreshed_at=START)
    mixed = _sibling("1111111111111111", KEY, "another key")
    clock.advance(timedelta(days=29))
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["recent"]), refreshed_at=clock.now)
    recent = _sibling("2222222222222222", KEY)

    clock.advance(timedelta(days=2))
    _serve(up)

    assert not old.exists()  # its only data went, then its locks and the folder
    [kept] = mixed.glob("*.json")  # another key's file is its own business
    assert json.loads(_lines(kept)[0])["untaped"]["key"] == "another key"
    assert len(list(recent.glob("*.json"))) == 1  # 2 days old: inside the horizon
    assert len(_files()) == 1


def test_a_busy_sibling_lock_keeps_its_file(clock: CacheClock, up: Controller) -> None:
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["old"]), refreshed_at=START)
    sibling = _sibling("0000000000000000", KEY)
    lock = sibling / f"{SNAPSHOTS._paths(KEY).stem}.lock"
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with FileLock(str(lock)):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(10)
    clock.advance(timedelta(days=31))
    try:
        _serve(up)
    finally:
        release.set()
        holder.join()
    assert len(list(sibling.glob("*.json"))) == 1


# --- partitions and splits -----------------------------------------------------------


def test_a_partition_keeps_its_own_files_and_never_shows_its_value(
    clock: CacheClock, up: Controller
) -> None:
    _serve(up, partition=TOKEN)
    up.items[KEY] = ["other"]
    served, _ = _serve(up, partition="another token")
    assert served.value[KEY] == Snapshot(items=["other"])
    assert up.fills == [KEY, KEY]
    names = " ".join(file.name for file in _files())
    assert TOKEN not in names and ".p" in names

    clock.advance(timedelta(hours=2))
    up.error = HttpTransportError("connection refused", system="toy")
    _, emitted = _serve(up, partition=TOKEN)
    [line] = _stale(emitted)
    assert TOKEN not in line.model_dump_json()
    assert SNAPSHOTS.freshness(KEY, partition=TOKEN).refreshed_at == START


def test_split_none_keeps_one_folder_for_every_profile(
    clock: CacheClock, up: Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = Cache("toy", "shared", label="x", max_age=timedelta(hours=1), model=Snapshot)
    shared_none = Cache(
        "toy", "shared", label="x", max_age=timedelta(hours=1), model=Snapshot, split="none"
    )
    assert shared._folder() == named_plugin_dir("toy") / "cached" / "shared" / settings_digest(
        "toy"
    )
    assert shared_none._folder() == named_plugin_dir("toy") / "cached" / "shared"
    _serve(up, cache=shared_none)
    monkeypatch.setenv("UNTAPED_PROFILE", "work")
    _serve(up, cache=shared_none)
    assert up.fills == [KEY]


# --- the declaration and the fill ----------------------------------------------------


def test_the_model_resolves_on_first_use_only(clock: CacheClock, up: Controller) -> None:
    calls: list[int] = []

    def model() -> type[Snapshot]:
        calls.append(1)
        return Snapshot

    lazy = Cache("toy", "lazy", label="x", max_age=timedelta(hours=1), model=model)
    assert calls == []
    _serve(up, cache=lazy)
    _serve(up, cache=lazy)
    assert calls == [1]


def test_what_a_typed_cache_refuses(clock: CacheClock, up: Controller) -> None:
    with pytest.raises(ConfigError, match="takes no read="):
        SNAPSHOTS.serve({KEY: "x"}, refresh=None, fill=up.fill, read=lambda: None)  # type: ignore[call-overload]
    with pytest.raises(ConfigError, match="a cache with model= holds its own"):
        SNAPSHOTS.seed(KEY, refreshed_at=START)
    plain = Cache("toy", "plain", label="x", max_age=timedelta(hours=1))
    with pytest.raises(ConfigError, match="needs read="):
        plain.serve({KEY: "x"}, refresh=None, fill=up.fill)  # type: ignore[call-overload]
    with pytest.raises(ConfigError, match="not a pydantic model"):
        Cache("toy", "bad", label="x", max_age=timedelta(hours=1), model=lambda: dict)._typing()  # type: ignore[arg-type,return-value]
    with pytest.raises(TypeError, match="declare it without model="):
        cached(SNAPSHOTS)  # type: ignore[arg-type]


def test_a_fill_must_return_the_value_its_cache_keeps(clock: CacheClock) -> None:
    def no_value(_key: str, _task: ProgressHandle) -> Filled[Snapshot]:
        return Filled[Snapshot](summary="forgot")

    def wrong(_key: str, _task: ProgressHandle) -> Filled[Any]:
        return Filled[Any](value={"items": []})

    with pytest.raises(TypeError, match="returned no value"):
        SNAPSHOTS.serve({KEY: "x"}, refresh=True, fill=no_value)
    with pytest.raises(TypeError, match="returned a dict, not the Snapshot"):
        SNAPSHOTS.serve({KEY: "x"}, refresh=True, fill=wrong)
    plain = Cache("toy", "plain", label="x", max_age=timedelta(hours=1))
    with pytest.raises(TypeError, match="never keeps"):
        plain.serve(
            {KEY: "x"},
            refresh=True,
            fill=lambda _k, _t: Filled[Any](value=1),
            read=lambda: None,
        )


def test_cached_peeks_at_the_kept_value_and_forget_drops_every_version(
    clock: CacheClock, up: Controller
) -> None:
    assert SNAPSHOTS.cached(KEY) is None
    _serve(up)
    keep_value(SNAPSHOTS, KEY, Snapshot(items=["old"]), refreshed_at=START, version="0")
    peek = SNAPSHOTS.cached(KEY)
    assert peek is not None and peek.value == Snapshot(items=["jt"]) and not peek.stale
    assert len(_files()) == 2

    SNAPSHOTS.forget(KEY)

    assert _files() == []
    assert SNAPSHOTS.cached(KEY) is None


def test_forget_leaves_a_key_whose_name_reads_like_a_version_of_another(
    clock: CacheClock,
) -> None:
    plain = Cache("toy", "plain", label="plain", max_age=timedelta(hours=1))
    assert plain.seed("x.vdeadbeef", refreshed_at=START)
    plain.forget("x")
    assert plain.freshness("x.vdeadbeef").refreshed_at == START


# --- 11.0's caches/ ------------------------------------------------------------------


def _legacy() -> Path:
    folder = named_plugin_dir("toy") / "caches" / "snapshots" / "default"
    folder.mkdir(parents=True)
    (folder / "https___c.example.json").write_text('{"untaped": "1"}', encoding="utf-8")
    return folder.parent.parent


def test_a_registered_install_removes_11_0s_caches_folder_after_a_complete_fill(
    clock: CacheClock, up: Controller
) -> None:
    legacy = _legacy()
    refs.register([])

    _, emitted = _serve(up)

    assert not legacy.exists()
    [line] = [each for each in emitted if isinstance(each, Pruned)]
    assert (line.plugin, line.store, line.layout, line.path) == ("toy", None, None, str(legacy))
    assert line.bytes == len('{"untaped": "1"}')
    assert "left by untaped 11.0" in line.message


def test_an_unregistered_install_or_a_failed_fill_keeps_it(
    clock: CacheClock, up: Controller
) -> None:
    legacy = _legacy()
    _serve(up)
    assert legacy.is_dir()

    refs.register([])
    clock.advance(timedelta(hours=2))
    up.error = HttpTransportError("connection refused", system="toy")
    _serve(up)
    assert legacy.is_dir()
