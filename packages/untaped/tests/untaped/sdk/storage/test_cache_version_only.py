"""``Cache(max_age=None)``: a value refreshed by its version alone, quietly, with no setting."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from core_test_support.commands.support import make_plugin
from untaped.runtime import bootstrap
from untaped.runtime.registry import compose, named_plugin_dir
from untaped.runtime.run import invocation
from untaped.runtime.settings import registered_profile_model
from untaped.sdk._run import recording
from untaped.sdk.diagnostic import Failure
from untaped.sdk.errors import ConfigError, UntapedError
from untaped.sdk.health.deprecated_keys import Retired
from untaped.sdk.progress import ProgressHandle
from untaped.sdk.storage.cache import Cache, Filled, NeverRefreshed, refresh_option
from untaped.sdk.storage.store import Store
from untaped.sdk.testing import CacheClock, cache_clock, damage, plugin_candidate

START = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
KEY = "everything"


class Composed(BaseModel):
    """What the toy composition found."""

    names: tuple[str, ...]


CODE = {"version": "1"}
COMPOSED = Cache(
    "toy",
    "composed",
    label="composed toys",
    max_age=None,
    model=Composed,
    version=lambda: CODE["version"],
    split="none",
)


@dataclass
class Composer:
    names: tuple[str, ...] = ("a",)
    error: Exception | None = None
    fills: list[str] = field(default_factory=list)

    def fill(self, key: str, _task: ProgressHandle) -> Filled[Composed]:
        self.fills.append(key)
        if self.error is not None:
            raise self.error
        return Filled(value=Composed(names=self.names))


@pytest.fixture
def clock() -> Iterator[CacheClock]:
    CODE["version"] = "1"
    with cache_clock(START) as wall:
        yield wall


@pytest.fixture
def up() -> Composer:
    return Composer()


def _serve(up: Composer, *, refresh: bool | None = None) -> tuple[Composed, list[Any]]:
    with invocation(), recording() as lines:
        served = COMPOSED.serve({KEY: "all toys"}, refresh=refresh, fill=up.fill)
    return served.value[KEY], list(lines)


def _files() -> list[str]:
    folder = named_plugin_dir("toy") / "cached" / "composed"
    return sorted(each.name for each in folder.iterdir() if each.suffix == ".json")


def test_it_fills_once_then_serves_its_file_with_nothing_on_stderr(
    clock: CacheClock, up: Composer
) -> None:
    assert _serve(up) == (Composed(names=("a",)), [])
    clock.advance(timedelta(days=400))  # no max age: time alone never refreshes it
    up.names = ("b",)
    assert _serve(up) == (Composed(names=("a",)), [])
    assert up.fills == [KEY]


def test_another_version_is_never_served_and_its_file_goes_after_the_fill(
    clock: CacheClock, up: Composer
) -> None:
    _serve(up)
    first = _files()
    CODE["version"] = "2"
    up.names = ("b",)
    assert _serve(up) == (Composed(names=("b",)), [])
    assert len(_files()) == 1
    assert _files() != first


def test_a_failed_fill_raises_and_no_other_version_stands_in(
    clock: CacheClock, up: Composer
) -> None:
    _serve(up)
    CODE["version"] = "2"
    up.error = UntapedError("cannot compose", category="config")
    with invocation(), recording() as lines, pytest.raises(UntapedError, match="compose"):
        COMPOSED.serve({KEY: "all toys"}, refresh=None, fill=up.fill)
    assert list(lines) == []  # no task, no stale line, no hint


def test_a_partial_fill_is_served_but_not_kept(clock: CacheClock, up: Composer) -> None:
    def partial(key: str, task: ProgressHandle) -> Filled[Composed]:
        up.fill(key, task)
        return Filled(value=Composed(names=up.names), partial=Failure.plain("one left out"))

    with invocation():
        served = COMPOSED.serve({KEY: "all toys"}, refresh=None, fill=partial)
    assert served.value[KEY] == Composed(names=("a",))
    assert served.freshness[KEY].partial
    assert COMPOSED.cached(KEY) is None
    _serve(up)
    assert up.fills == [KEY, KEY]


def test_a_damaged_file_is_filled_again(clock: CacheClock, up: Composer) -> None:
    _serve(up)
    damage(COMPOSED, KEY)
    assert _serve(up) == (Composed(names=("a",)), [])
    assert up.fills == [KEY, KEY]


def test_pruning_keeps_another_key_and_another_partition(clock: CacheClock, up: Composer) -> None:
    with invocation():
        COMPOSED.serve({"other": "other toys"}, refresh=None, fill=up.fill)
        COMPOSED.serve({KEY: "all toys"}, refresh=None, fill=up.fill, partition="p")
    _serve(up)
    CODE["version"] = "2"
    _serve(up)
    assert COMPOSED.cached(KEY) is not None
    CODE["version"] = "1"
    assert COMPOSED.cached("other") is not None
    assert COMPOSED.cached(KEY, partition="p") is not None


def test_freshness_reads_this_version_only(clock: CacheClock, up: Composer) -> None:
    _serve(up)
    assert COMPOSED.freshness(KEY).refreshed_at == START
    CODE["version"] = "2"
    assert COMPOSED.freshness(KEY).refreshed_at is None


def test_a_home_it_cannot_write_serves_the_fill_from_memory(
    clock: CacheClock, up: Composer
) -> None:
    named_plugin_dir("toy").parent.mkdir(parents=True, exist_ok=True)
    named_plugin_dir("toy").write_text("not a folder", encoding="utf-8")
    assert _serve(up) == (Composed(names=("a",)), [])
    assert _serve(up) == (Composed(names=("a",)), [])
    assert up.fills == [KEY, KEY]


def test_refresh_refills_and_cached_never_fills(clock: CacheClock, up: Composer) -> None:
    with pytest.raises(NeverRefreshed):
        _serve(up, refresh=False)
    _serve(up)
    up.names = ("b",)
    assert _serve(up, refresh=False)[0] == Composed(names=("a",))
    assert _serve(up, refresh=True)[0] == Composed(names=("b",))
    assert up.fills == [KEY, KEY]


def test_cached_peeks_at_this_version_only(clock: CacheClock, up: Composer) -> None:
    assert COMPOSED.cached(KEY) is None
    _serve(up)
    peeked = COMPOSED.cached(KEY)
    assert peeked is not None and peeked.value == Composed(names=("a",))
    CODE["version"] = "2"
    assert COMPOSED.cached(KEY) is None


def _composed() -> Cache[Composed]:
    """A version-only cache like ``COMPOSED``, for one plugin class to hold."""
    return Cache(
        label="composed toys",
        max_age=None,
        model=Composed,
        version=lambda: CODE["version"],
        split="none",
    )


def _section(**caches: Cache[Any]) -> type[BaseModel] | None:
    """Plugin ``toy``'s composed config section when it holds ``caches``."""
    with bootstrap.kept_composition():
        bootstrap.compose_root(candidates=[plugin_candidate(make_plugin("toy", **caches))])
        return registered_profile_model("toy")


def test_it_creates_no_setting() -> None:
    timed = Cache(label="timed", max_age=timedelta(hours=1))
    section = _section(composed=_composed(), timed=timed)
    assert section is not None
    assert set(section.model_fields["caches"].annotation.model_fields) == {"timed"}  # type: ignore[union-attr]
    assert "caches" not in (_section(composed=_composed()) or BaseModel).model_fields
    with pytest.raises(ConfigError, match="version-only"):
        COMPOSED.max_age()
    with pytest.raises(ConfigError, match="version-only"):
        COMPOSED.setting  # noqa: B018 - the property raises


def test_a_plugin_with_only_version_only_caches_may_retire_an_old_caches_key() -> None:
    class Own(BaseModel):
        retired_keys: ClassVar[Mapping[str, str | Retired]] = {"caches.old": Retired(note="gone")}

    toy = make_plugin("toy", settings=Own, composed=_composed())
    result = compose([plugin_candidate(toy)])
    assert [plugin.spec.name for plugin in result.plugins] == ["untaped", "toy"], result.quarantine


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"version": lambda: "1"}, "takes model= and version="),
        ({"model": Composed}, "takes model= and version="),
        ({"model": Composed, "version": lambda: "1", "split": "settings"}, 'split="none"'),
        (
            {"model": Composed, "version": lambda: "1", "renamed_from": ("old",)},
            "no setting to rename",
        ),
        (
            {"model": Composed, "version": lambda: "1", "store": Store("toy", "repos", base=Path)},
            "follows no place or store",
        ),
    ],
)
def test_a_version_only_declaration_needs_its_model_and_version(
    options: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        Cache("toy", "bad", label="bad", max_age=None, **({"split": "none"} | options))


def test_refresh_flags_refuse_a_version_only_cache() -> None:
    with pytest.raises(ConfigError, match="version-only"):
        refresh_option(COMPOSED)
