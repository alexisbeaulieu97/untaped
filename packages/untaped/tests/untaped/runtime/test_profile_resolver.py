"""Tests for the pure ``profile_resolver`` helper.

The resolver layers each picked ``profiles.<name>`` in order over the base,
``config.yml``'s top level (bottom), and returns ``(effective_dict,
provenance_dict)``. ``provenance_dict`` maps every leaf path to the
profile name that supplied that value (``default`` for the base).
"""

from __future__ import annotations

import pytest

from untaped.runtime.profile_resolver import (
    Picked,
    base_sections,
    current_pick,
    profile_entries,
    profile_scope,
    resolve_profiles,
    selected_profile,
    write_target,
)
from untaped.sdk.errors import ConfigError, UsageError


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
    effective, provenance = resolve_profiles(config, picks=("prod",))
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
    effective, provenance = resolve_profiles(config, picks=("default",))
    assert effective == {"awx": {"token": "d"}}
    assert provenance == {("awx", "token"): "default"}


def test_an_unknown_pick_errors() -> None:
    config = {"format_version": 2, "awx": {"token": "d"}}
    with pytest.raises(ConfigError) as excinfo:
        resolve_profiles(config, picks=("nope",))
    assert "nope" in str(excinfo.value)


def test_nested_dicts_are_deep_merged() -> None:
    config = {
        "format_version": 2,
        "awx": {"api_prefix": "/api/v2/"},
        "untaped": {"http": {"timeout_seconds": 5, "ca_bundle_path": "/default/ca"}},
        "profiles": {"prod": {"untaped": {"http": {"ca_bundle_path": "/prod/ca"}}}},
    }
    effective, provenance = resolve_profiles(config, picks=("prod",))
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
    effective, provenance = resolve_profiles(config, picks=("prod",))
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


def test_picks_layer_in_order_later_wins() -> None:
    config = {
        "awx": {"a": "base", "b": "base", "c": "base"},
        "profiles": {"one": {"awx": {"a": "one", "b": "one"}}, "two": {"awx": {"b": "two"}}},
    }
    effective, provenance = resolve_profiles(config, picks=("one", "two"))
    assert effective == {"awx": {"a": "one", "b": "two", "c": "base"}}
    assert provenance == {("awx", "a"): "one", ("awx", "b"): "two", ("awx", "c"): "default"}


def test_an_unknown_pick_is_a_usage_error_naming_the_known() -> None:
    with pytest.raises(ConfigError) as refused:
        resolve_profiles({"profiles": {"prod": {}}}, picks=("prod", "nope"))
    assert refused.value.exit_code == 2
    assert refused.value.details == {"known": ["default", "prod"]}


# --------------------------------- the pick --------------------------------- #


def test_the_variable_picks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "stage")
    assert current_pick() == (Picked("stage", "env"),)


def test_the_flag_stacks_after_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "a, b")
    with profile_scope(("c", "a")):
        assert current_pick() == (Picked("b", "env"), Picked("c", "flag"), Picked("a", "flag"))
        assert selected_profile() == "b,c,a"


def test_flag_default_reads_the_base_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "stage")
    with profile_scope("default"):
        assert current_pick() == ()
        assert selected_profile() == "default"


def test_variable_default_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "default")
    assert current_pick() == ()
    with profile_scope("prod"):
        assert current_pick() == (Picked("prod", "flag"),)


def test_nothing_picks_without_flag_or_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    assert current_pick() == ()
    monkeypatch.setenv("UNTAPED_PROFILE", "")
    assert current_pick() == ()
    assert selected_profile() == "default"


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("a,,b", "invalid profile name ''"),
        ("a b", "invalid profile name 'a b'"),
        ("-x", "invalid profile name '-x'"),
        ("a,default", "stacks `default`"),
    ],
)
def test_a_malformed_variable_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, value: str, match: str
) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", value)
    with pytest.raises(ConfigError, match=match) as refused:
        current_pick()
    assert refused.value.exit_code == 2


def test_writes_go_to_the_one_flag_profile_else_the_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "stage")
    assert write_target() == "default"  # the variable picks reads, never writes
    with profile_scope("prod"):
        assert write_target() == "prod"
    with profile_scope(("prod", "lab")), pytest.raises(UsageError) as refused:
        write_target()
    assert refused.value.details == {"profiles": ["prod", "lab"]}


@pytest.mark.parametrize("config", [{}, {"profiles": {}}, {"profiles": None}])
def test_an_unknown_pick_errors_even_without_profiles(config: dict[str, object]) -> None:
    with pytest.raises(ConfigError, match="'prod'"):
        resolve_profiles(config, picks=("prod",))


def test_picking_default_resolves_without_profiles() -> None:
    assert resolve_profiles({}, picks=("default",)) == ({}, {})


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
    assert resolve_profiles({"profiles": {"prod": None}}, picks=("prod",)) == ({}, {})
