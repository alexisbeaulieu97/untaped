"""Tests for the pure ``profile_resolver`` helper.

The resolver merges ``profiles.default`` (bottom) with ``profiles.<active>``
(top) and returns ``(effective_dict, provenance_dict)``. ``provenance_dict``
maps every leaf path to the profile name that supplied that value.
"""

from __future__ import annotations

import pytest
from untaped_core.errors import ConfigError
from untaped_core.profile_resolver import resolve_profiles


def test_empty_config_returns_empty() -> None:
    effective, provenance = resolve_profiles({})
    assert effective == {}
    assert provenance == {}


def test_default_only_uses_default_profile() -> None:
    config = {"profiles": {"default": {"awx": {"token": "x"}}}}
    effective, provenance = resolve_profiles(config)
    assert effective == {"awx": {"token": "x"}}
    assert provenance == {("awx", "token"): "default"}


def test_active_overrides_default_per_leaf() -> None:
    config = {
        "profiles": {
            "default": {"awx": {"token": "d-tok", "api_prefix": "/api/v2/"}},
            "prod": {"awx": {"token": "p-tok", "base_url": "https://prod"}},
        },
        "active": "prod",
    }
    effective, provenance = resolve_profiles(config)
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


def test_active_override_wins_over_config_active() -> None:
    config = {
        "profiles": {
            "default": {"awx": {"token": "d"}},
            "prod": {"awx": {"token": "p"}},
            "stage": {"awx": {"token": "s"}},
        },
        "active": "prod",
    }
    effective, provenance = resolve_profiles(config, active_override="stage")
    assert effective == {"awx": {"token": "s"}}
    assert provenance == {("awx", "token"): "stage"}


def test_active_falls_back_to_default_when_unset() -> None:
    config = {"profiles": {"default": {"awx": {"token": "d"}}}}
    effective, provenance = resolve_profiles(config)
    assert effective == {"awx": {"token": "d"}}
    assert provenance == {("awx", "token"): "default"}


def test_active_explicitly_named_default_works() -> None:
    config = {
        "profiles": {"default": {"awx": {"token": "d"}}},
        "active": "default",
    }
    effective, provenance = resolve_profiles(config)
    assert effective == {"awx": {"token": "d"}}
    assert provenance == {("awx", "token"): "default"}


def test_missing_default_with_profiles_present_errors() -> None:
    config = {"profiles": {"prod": {"awx": {"token": "p"}}}}
    with pytest.raises(ConfigError) as excinfo:
        resolve_profiles(config)
    assert "default" in str(excinfo.value)


def test_missing_active_profile_errors() -> None:
    config = {
        "profiles": {"default": {"awx": {"token": "d"}}},
        "active": "nonexistent",
    }
    with pytest.raises(ConfigError) as excinfo:
        resolve_profiles(config)
    assert "nonexistent" in str(excinfo.value)


def test_missing_active_override_errors() -> None:
    config = {"profiles": {"default": {"awx": {"token": "d"}}}}
    with pytest.raises(ConfigError) as excinfo:
        resolve_profiles(config, active_override="nope")
    assert "nope" in str(excinfo.value)


def test_nested_dicts_are_deep_merged() -> None:
    config = {
        "profiles": {
            "default": {
                "http": {"verify_ssl": True, "ca_bundle": "/default/ca"},
                "awx": {"api_prefix": "/api/v2/"},
            },
            "prod": {
                "http": {"ca_bundle": "/prod/ca"},
            },
        },
        "active": "prod",
    }
    effective, provenance = resolve_profiles(config)
    assert effective == {
        "http": {"verify_ssl": True, "ca_bundle": "/prod/ca"},
        "awx": {"api_prefix": "/api/v2/"},
    }
    assert provenance == {
        ("http", "verify_ssl"): "default",
        ("http", "ca_bundle"): "prod",
        ("awx", "api_prefix"): "default",
    }


def test_lists_are_replaced_not_merged() -> None:
    """A list in the active profile replaces (not extends) the default's list."""
    config = {
        "profiles": {
            "default": {"some_list": [1, 2, 3]},
            "prod": {"some_list": [4]},
        },
        "active": "prod",
    }
    effective, provenance = resolve_profiles(config)
    assert effective == {"some_list": [4]}
    assert provenance == {("some_list",): "prod"}


def test_active_falls_back_to_default_when_active_is_empty_string() -> None:
    """An empty `active:` line in YAML should not crash; treat as unset."""
    config = {
        "profiles": {"default": {"awx": {"token": "d"}}},
        "active": "",
    }
    effective, provenance = resolve_profiles(config)
    assert effective == {"awx": {"token": "d"}}
    assert provenance == {("awx", "token"): "default"}


def test_keys_outside_profiles_are_ignored_by_resolver() -> None:
    """The resolver only merges `profiles.*`; top-level state (e.g. `workspace`)
    is the caller's responsibility (see ProfilesSettingsSource)."""
    config = {
        "profiles": {"default": {"awx": {"token": "d"}}},
        "workspace": {"workspaces": [{"name": "x", "path": "/p"}]},
    }
    effective, provenance = resolve_profiles(config)
    assert effective == {"awx": {"token": "d"}}
    assert provenance == {("awx", "token"): "default"}
