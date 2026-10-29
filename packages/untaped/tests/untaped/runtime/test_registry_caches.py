"""A plugin's caches: what composition quarantines and what it injects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from core_test_support.commands.support import make_plugin
from core_test_support.contracts.support import BookSource
from core_test_support.runtime.plugin_harness import make_candidate
from untaped.runtime.composed import ComposedPlugin
from untaped.runtime.registry import (
    CompositionResult,
    compose,
    section_settings,
)
from untaped.sdk import Plugin
from untaped.sdk.errors import ConfigError
from untaped.sdk.storage.cache import Cache


class HelloSettings(BaseModel):
    greeting: str = "hi"


class NoSettings(BaseModel):
    """A plugin's settings with no field."""


def _compose(plugin: type[Plugin]) -> CompositionResult:
    return compose([make_candidate(plugin)])


def _reason(plugin: type[Plugin]) -> tuple[str, str]:
    (record,) = _compose(plugin).quarantine
    return record.reason, record.detail


def _motd(*old: str) -> Cache[Any]:
    """A fresh ``motd`` cache for each double: a block belongs to one plugin class."""
    return Cache(label="message of the day", max_age=timedelta(hours=1), renamed_from=old)


def test_a_plugin_composes_its_caches() -> None:
    hello = make_plugin("hello", settings=HelloSettings, motd=_motd())
    result = _compose(hello)
    assert result.quarantine == ()
    assert [cache.qualified for cache in result.plugins[1].caches] == ["hello.motd"]


def test_a_plugin_with_caches_and_no_settings_gets_a_section() -> None:
    section = section_settings(_hello(settings=NoSettings, motd=_motd()))
    assert list(section.model_fields) == ["caches"]


def test_a_plugin_without_caches_keeps_its_own_model() -> None:
    assert section_settings(_hello(settings=HelloSettings)) is HelloSettings


def test_an_owner_with_caches_gets_extensions_and_caches() -> None:
    books = Cache(label="books", max_age=timedelta(days=1))
    shelf = make_plugin("shelf", settings=HelloSettings, offers=(BookSource,), books=books)
    section = section_settings(_compose(shelf).plugins[1])
    assert list(section.model_fields) == ["greeting", "extensions", "caches"]


def _hello(**attributes: Any) -> ComposedPlugin:
    """Plugin ``hello`` with ``attributes``, composed."""
    result = _compose(make_plugin("hello", **attributes))
    assert result.quarantine == ()
    return result.plugins[1]


class OwnOldKeyUnderCaches(BaseModel):
    renamed_keys: ClassVar[Mapping[str, str]] = {"caches.old": "greeting"}

    greeting: str = "hi"


def test_an_own_old_key_under_caches_collides_once_caches_are_injected() -> None:
    plain = make_plugin("hello", settings=OwnOldKeyUnderCaches)
    assert _compose(plain).quarantine == ()
    reason, detail = _reason(make_plugin("hello", settings=OwnOldKeyUnderCaches, motd=_motd()))
    assert reason == "bad-settings-keys"
    assert "caches, which untaped reserves" in detail


def _held(*old: str, settings: type[BaseModel] = HelloSettings) -> type[Plugin]:
    """Plugin ``hello`` holding a ``motd`` cache renamed from ``old``."""
    return make_plugin("hello", settings=settings, motd=_motd(*old))


class Retiring(BaseModel):
    renamed_keys: ClassVar[Mapping[str, str]] = {"salutation": "greeting"}
    retired_keys: ClassVar[Mapping[str, str]] = {"motd_hours": "greeting"}

    greeting: str = "hi"


@pytest.mark.parametrize(
    ("spec", "names"),
    [
        (lambda: _held("greeting"), "clashes with the current setting 'greeting'"),
        (lambda: _held("salutation", settings=Retiring), "'salutation' is in Retiring"),
        (lambda: _held("motd_hours", settings=Retiring), "'motd_hours' is in Retiring"),
    ],
)
def test_a_renamed_from_the_owner_holds_is_refused(spec: object, names: str) -> None:
    reason, detail = _reason(spec())  # type: ignore[operator]
    assert reason == "bad-settings-keys"
    assert "caches renamed_from" in detail
    assert names in detail


def test_an_old_key_two_caches_rename_is_refused() -> None:
    caches = {
        name: Cache(label=name, max_age=timedelta(0), renamed_from=("ttl_seconds",))
        for name in ("motd", "news")
    }
    reason, detail = _reason(make_plugin("hello", settings=HelloSettings, **caches))
    assert reason == "bad-settings-keys"
    assert "'ttl_seconds' is renamed by both caches 'motd' and 'news'" in detail


@pytest.mark.parametrize(
    ("old", "problem"),
    [
        ("motd_hours", None),
        ("feed.max_age_seconds", None),
        ("Motd", "dotted settings segments"),
        ("feed..max_age", "dotted settings segments"),
        ("caches.motd.max_age_seconds", "under caches"),
        ("caches", "under caches"),
    ],
)
def test_renamed_from_is_checked_for_shape_at_declaration(old: str, problem: str | None) -> None:
    if problem is None:
        assert _hello(settings=HelloSettings, motd=_motd(old)).caches[0].renamed_from == (old,)
        return
    with pytest.raises(ConfigError, match=problem):
        _motd(old)._spelled_as("hello", "motd")


@pytest.mark.parametrize(
    ("old", "problem"),
    [("motd_hours", "not one string"), (("a", "a"), "names a key twice")],
)
def test_renamed_from_is_a_sequence_of_distinct_keys(
    old: str | Sequence[str], problem: str
) -> None:
    with pytest.raises(ConfigError, match=problem):
        Cache(label="m", max_age=timedelta(0), renamed_from=old)._spelled_as("hello", "motd")


def test_max_age_is_whole_seconds() -> None:
    with pytest.raises(ConfigError, match="whole seconds"):
        Cache(label="m", max_age=timedelta(milliseconds=1500))._spelled_as("hello", "motd")


def test_each_composition_hands_over_the_same_cache_and_settings_model() -> None:
    """Caches hash by identity and key the settings model, so a recomposition reuses both."""
    hello = make_plugin("hello", settings=HelloSettings, motd=_motd())
    first, second = (_compose(hello).plugins[1] for _ in range(2))
    assert first.caches[0] is second.caches[0]
    assert section_settings(first) is section_settings(second)
