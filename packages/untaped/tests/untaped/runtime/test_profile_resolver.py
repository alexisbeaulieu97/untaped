"""Tests for the pure ``profile_resolver`` helper.

The resolver layers the picked ``profiles.<name>`` (top) over the base,
``config.yml``'s top level (bottom), and returns ``(effective_dict,
provenance_dict)``. ``provenance_dict`` maps every leaf path to the
profile name that supplied that value (``default`` for the base).
"""

from __future__ import annotations

import pytest

from untaped.runtime.profile_resolver import (
    base_sections,
    classify_active_profile,
    effective_active_profile_name,
    profile_entries,
    resolve_profiles,
    set_profile_override,
)
from untaped.sdk.errors import ConfigError


def test_empty_config_returns_empty() -> None:
    effective, provenance = resolve_profiles({})
    assert effective == {}
    assert provenance == {}


def test_the_base_alone_is_read_without_a_pick() -> None:
    config = {"format_version": 2, "awx": {"token": "x"}}
    effective, provenance = resolve_profiles(config)
    assert effective == {"awx": {"token": "x"}}
    assert provenance == {("awx", "token"): "default"}


def test_the_pick_overrides_the_base_per_leaf() -> None:
    config = {
        "format_version": 2,
        "awx": {"token": "d-tok", "api_prefix": "/api/v2/"},
        "profiles": {"prod": {"awx": {"token": "p-tok", "base_url": "https://prod"}}},
    }
    effective, provenance = resolve_profiles(config, active_override="prod")
    assert effective == {
        "awx": {
            "token": "p-tok",
            "api_prefix": "/api/v2/",
            "base_url": "https://prod",
        }
    }
    assert provenance == {
        ("awx", "token"): "prod",
        ("awx", "api_prefix"): "default",
        ("awx", "base_url"): "prod",
    }


def test_named_profiles_are_read_only_when_picked() -> None:
    config = {"format_version": 2, "profiles": {"prod": {"awx": {"token": "p"}}}}
    assert resolve_profiles(config) == ({}, {})


def test_picking_default_reads_the_base_alone() -> None:
    config = {"format_version": 2, "awx": {"token": "d"}, "profiles": {"prod": {}}}
    effective, provenance = resolve_profiles(config, active_override="default")
    assert effective == {"awx": {"token": "d"}}
    assert provenance == {("awx", "token"): "default"}


def test_an_unknown_pick_errors() -> None:
    config = {"format_version": 2, "awx": {"token": "d"}}
    with pytest.raises(ConfigError) as excinfo:
        resolve_profiles(config, active_override="nope")
    assert "nope" in str(excinfo.value)


def test_nested_dicts_are_deep_merged() -> None:
    config = {
        "format_version": 2,
        "awx": {"api_prefix": "/api/v2/"},
        "untaped": {"http": {"timeout_seconds": 5, "ca_bundle_path": "/default/ca"}},
        "profiles": {"prod": {"untaped": {"http": {"ca_bundle_path": "/prod/ca"}}}},
    }
    effective, provenance = resolve_profiles(config, active_override="prod")
    assert effective == {
        "awx": {"api_prefix": "/api/v2/"},
        "untaped": {"http": {"timeout_seconds": 5, "ca_bundle_path": "/prod/ca"}},
    }
    assert provenance == {
        ("untaped", "http", "timeout_seconds"): "default",
        ("untaped", "http", "ca_bundle_path"): "prod",
        ("awx", "api_prefix"): "default",
    }


def test_lists_are_replaced_not_merged() -> None:
    """A list in the picked profile replaces (not extends) the base's list."""
    config = {"some_list": [1, 2, 3], "profiles": {"prod": {"some_list": [4]}}}
    effective, provenance = resolve_profiles(config, active_override="prod")
    assert effective == {"some_list": [4]}
    assert provenance == {("some_list",): "prod"}


def test_the_file_keys_are_not_base_sections() -> None:
    config = {"format_version": 2, "awx": {}, "profiles": {"prod": {}}}
    assert base_sections(config) == {"awx": {}}


def test_profile_entries_list_the_base_first_with_their_place() -> None:
    config = {"format_version": 2, "profiles": {"b": {}, "a": {"x": 1}, "bad": 3}}
    assert [(name, path) for name, _, path in profile_entries(config)] == [
        ("default", ()),
        ("a", ("profiles", "a")),
        ("b", ("profiles", "b")),
    ]
    assert next(profile_entries(config))[1] is config


# ----------------------- active-profile classification ----------------------- #


def test_classify_active_profile_reads_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "stage")
    assert classify_active_profile() == ("stage", "env")


def test_profile_override_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "env-prof")
    set_profile_override("flag-prof")
    try:
        assert classify_active_profile() == ("flag-prof", "flag")
    finally:
        set_profile_override(None)


def test_classify_active_profile_falls_back_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    assert classify_active_profile() == (None, "fallback")
    monkeypatch.setenv("UNTAPED_PROFILE", "")
    assert classify_active_profile() == (None, "fallback")


def test_effective_active_profile_name_matches_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "stage")
    assert effective_active_profile_name() == "stage"
    monkeypatch.delenv("UNTAPED_PROFILE")
    assert effective_active_profile_name() is None


@pytest.mark.parametrize("config", [{}, {"profiles": {}}, {"profiles": None}])
def test_an_unknown_pick_errors_even_without_profiles(config: dict[str, object]) -> None:
    with pytest.raises(ConfigError, match="'prod'"):
        resolve_profiles(config, active_override="prod")


def test_picking_default_resolves_without_profiles() -> None:
    assert resolve_profiles({}, active_override="default") == ({}, {})


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"profiles": ["a"]}, "'profiles' must be a mapping"),
        ({"profiles": {"prod": "x"}}, "profile 'prod' must be a mapping"),
        ({"profiles": {"default": {}}}, "profiles.default is not read"),
    ],
)
def test_malformed_profiles_raise_config_error(config: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        resolve_profiles(config)


def test_null_profile_entry_is_an_empty_profile() -> None:
    assert resolve_profiles({"profiles": {"prod": None}}, active_override="prod") == ({}, {})
