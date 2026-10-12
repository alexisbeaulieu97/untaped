"""``PluginSpec.caches``: what composition accepts, what it quarantines, what it injects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import ClassVar

import pytest
from pydantic import BaseModel

from test_contracts.support import BookSource
from test_plugins.plugin_harness import make_candidate, make_shell, make_spec
from untaped.cache import Cache
from untaped.errors import ConfigError
from untaped.plugins.registry import (
    VALID_REASONS,
    CompositionResult,
    PluginSpec,
    compose,
    section_settings,
)

MOTD = Cache("hello", "motd", label="message of the day", max_age=timedelta(hours=1))


class HelloSettings(BaseModel):
    greeting: str = "hi"


def _compose(spec: PluginSpec) -> CompositionResult:
    return compose(make_shell(), [make_candidate(spec)])


def _reason(spec: PluginSpec) -> tuple[str, str]:
    (record,) = _compose(spec).quarantine
    return record.reason, record.detail


def test_both_cache_reasons_are_valid() -> None:
    assert {"foreign-cache", "duplicate-cache"} <= VALID_REASONS


def test_a_plugin_composes_its_caches() -> None:
    spec = PluginSpec(name="hello", settings=HelloSettings, caches=[MOTD])  # type: ignore[arg-type]
    result = _compose(spec)
    assert result.quarantine == ()
    assert result.plugins[0].spec.caches == (MOTD,)


def test_a_cache_of_another_plugin_is_quarantined() -> None:
    other = Cache("other", "motd", label="message of the day", max_age=timedelta(hours=1))
    reason, detail = _reason(PluginSpec(name="hello", caches=(other,)))
    assert reason == "foreign-cache"
    assert "'other.motd'" in detail


def test_a_cache_declared_twice_is_quarantined() -> None:
    again = Cache("hello", "motd", label="another", max_age=timedelta(0))
    reason, detail = _reason(PluginSpec(name="hello", caches=(MOTD, again)))
    assert reason == "duplicate-cache"
    assert "'motd' twice" in detail


def test_caches_holds_only_caches() -> None:
    with pytest.raises(ConfigError, match="not a Cache"):
        PluginSpec(name="hello", caches=("motd",))  # type: ignore[arg-type]


def test_a_spec_whose_caches_fail_at_import_is_a_malformed_entry_point() -> None:
    candidate = make_candidate(
        make_spec(name="hello"), error=ConfigError("plugin 'hello' caches holds 'motd'")
    )
    (record,) = compose(make_shell(), [candidate]).quarantine
    assert record.reason == "malformed-entry-point"


def test_a_plugin_with_caches_and_no_settings_gets_a_section() -> None:
    section = section_settings(PluginSpec(name="hello", caches=(MOTD,)))
    assert section is not None
    assert list(section.model_fields) == ["caches"]


def test_a_plugin_without_caches_keeps_its_own_model() -> None:
    assert section_settings(PluginSpec(name="hello", settings=HelloSettings)) is HelloSettings


def test_an_owner_with_caches_gets_extensions_and_caches() -> None:
    owner = PluginSpec(
        name="shelf", settings=HelloSettings, contracts=lambda: (BookSource,), caches=(SHELF,)
    )
    section = section_settings(owner)
    assert section is not None
    assert list(section.model_fields) == ["greeting", "extensions", "caches"]


SHELF = Cache("shelf", "books", label="books", max_age=timedelta(days=1))


class OwnOldKeyUnderCaches(BaseModel):
    renamed_keys: ClassVar[Mapping[str, str]] = {"caches.old": "greeting"}

    greeting: str = "hi"


def test_an_own_old_key_under_caches_collides_once_caches_are_injected() -> None:
    plain = PluginSpec(name="hello", settings=OwnOldKeyUnderCaches)
    assert _compose(plain).quarantine == ()
    reason, detail = _reason(
        PluginSpec(name="hello", settings=OwnOldKeyUnderCaches, caches=(MOTD,))
    )
    assert reason == "bad-settings-keys"
    assert "caches, which untaped reserves" in detail


def _renamed(*old: str, settings: type[BaseModel] = HelloSettings) -> PluginSpec:
    cache = Cache(
        "hello", "motd", label="message of the day", max_age=timedelta(hours=1), renamed_from=old
    )
    return PluginSpec(name="hello", settings=settings, caches=(cache,))


class Retiring(BaseModel):
    renamed_keys: ClassVar[Mapping[str, str]] = {"salutation": "greeting"}
    retired_keys: ClassVar[Mapping[str, str]] = {"motd_hours": "greeting"}

    greeting: str = "hi"


@pytest.mark.parametrize(
    ("spec", "names"),
    [
        (lambda: _renamed("greeting"), "clashes with the current setting 'greeting'"),
        (lambda: _renamed("salutation", settings=Retiring), "'salutation' is in Retiring"),
        (lambda: _renamed("motd_hours", settings=Retiring), "'motd_hours' is in Retiring"),
    ],
)
def test_a_renamed_from_the_owner_holds_is_refused(spec: object, names: str) -> None:
    reason, detail = _reason(spec())  # type: ignore[operator]
    assert reason == "bad-settings-keys"
    assert "caches renamed_from" in detail
    assert names in detail


def test_an_old_key_two_caches_rename_is_refused() -> None:
    caches = tuple(
        Cache("hello", name, label=name, max_age=timedelta(0), renamed_from=("ttl_seconds",))
        for name in ("motd", "news")
    )
    reason, detail = _reason(PluginSpec(name="hello", caches=caches))
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
        assert _renamed(old).caches[0].renamed_from == (old,)
        return
    with pytest.raises(ConfigError, match=problem):
        _renamed(old)


@pytest.mark.parametrize(
    ("old", "problem"),
    [("motd_hours", "not one string"), (("a", "a"), "names a key twice")],
)
def test_renamed_from_is_a_sequence_of_distinct_keys(
    old: str | Sequence[str], problem: str
) -> None:
    with pytest.raises(ConfigError, match=problem):
        Cache("hello", "motd", label="m", max_age=timedelta(0), renamed_from=old)


def test_max_age_is_whole_seconds() -> None:
    with pytest.raises(ConfigError, match="whole seconds"):
        Cache("hello", "motd", label="m", max_age=timedelta(milliseconds=1500))


def test_renamed_from_takes_part_in_equality() -> None:
    assert _renamed("old").caches[0] != MOTD
    assert _renamed("old").caches[0] == _renamed("old").caches[0]
