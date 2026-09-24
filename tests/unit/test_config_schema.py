"""Tests for the settings schema walker."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field, SecretStr

from untaped.config_schema import (
    find_descriptor,
    redact_secrets,
    redact_url_password,
    secret_field_paths,
    walk_settings,
)
from untaped.settings import (
    get_settings_model,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
)


class DemoProfileSettings(BaseModel):
    directory: Path = Path("~/.demo")
    token: SecretStr | None = None


class DemoStateSettings(BaseModel):
    entries: list[str] = Field(default_factory=list)


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    reset_config_registry_for_tests()
    register_profile_settings("demo", DemoProfileSettings)
    yield
    reset_config_registry_for_tests()


def test_walks_nested_models() -> None:
    descriptors = walk_settings(get_settings_model())
    keys = {d.key for d in descriptors}
    # Top-level scalar
    assert "log_level" in keys
    # HttpSettings
    assert "http.ca_bundle" in keys
    assert "http.verify_ssl" in keys
    # Plugin settings
    assert "demo.directory" in keys
    assert "demo.token" in keys


def test_skips_collection_fields() -> None:
    register_profile_settings("demo", DemoProfileSettings)
    register_state_settings("demo", DemoStateSettings)

    descriptors = walk_settings(get_settings_model())
    keys = {d.key for d in descriptors}
    # ``demo.entries`` is a list — should not appear. The sibling scalar
    # ``demo.directory`` must still appear, so a prefix check would be too broad.
    assert "demo.entries" not in keys
    assert "demo.directory" in keys


def test_include_collections_returns_collections_as_whole_leaves() -> None:
    class Nested(BaseModel):
        roles: dict[str, str] = Field(default_factory=dict)

    class Demo(BaseModel):
        entries: list[str] = Field(default_factory=list)
        nested: Nested = Field(default_factory=Nested)
        name: str = "x"

    descriptors = walk_settings(Demo, include_collections=True)

    assert [(d.key, d.is_collection) for d in descriptors] == [
        ("entries", True),
        ("nested.roles", True),
        ("name", False),
    ]
    assert [d.key for d in walk_settings(Demo)] == ["name"]


def test_secrets_are_marked() -> None:
    descriptors = walk_settings(get_settings_model())
    token = find_descriptor(descriptors, "demo.token")
    assert token is not None
    assert token.is_secret is True
    assert token.annotation is SecretStr

    log_level = find_descriptor(descriptors, "log_level")
    assert log_level is not None
    assert log_level.is_secret is False


def test_defaults_are_captured() -> None:
    descriptors = walk_settings(get_settings_model())
    log_level = find_descriptor(descriptors, "log_level")
    assert log_level is not None
    assert log_level.has_default
    assert log_level.default == "INFO"

    verify = find_descriptor(descriptors, "http.verify_ssl")
    assert verify is not None
    assert verify.default is True


def test_optional_unwrapped() -> None:
    descriptors = walk_settings(get_settings_model())
    ca_bundle = find_descriptor(descriptors, "http.ca_bundle")
    assert ca_bundle is not None
    assert ca_bundle.annotation is Path


def test_find_descriptor_returns_none_for_unknown() -> None:
    descriptors = walk_settings(get_settings_model())
    assert find_descriptor(descriptors, "does.not.exist") is None


def test_redact_secrets_replaces_secret_leaves() -> None:
    data: dict[str, Any] = {
        "awx": {"token": "xoxb-secret"},
        "external": {"token": "ghp_secret"},
    }
    out = redact_secrets(data, [("awx", "token"), ("external", "token")])
    assert out == {"awx": {"token": "***"}, "external": {"token": "***"}}
    # Source dict is not mutated.
    assert data["awx"]["token"] == "xoxb-secret"


def test_redact_secrets_preserves_none() -> None:
    data: dict[str, Any] = {"awx": {"token": None}}
    out = redact_secrets(data, [("awx", "token")])
    assert out == {"awx": {"token": None}}


def test_redact_secrets_skips_missing_paths() -> None:
    # Profile-shaped data may omit any subset of the schema; missing
    # paths are silently skipped rather than raising.
    data: dict[str, Any] = {"awx": {}}
    out = redact_secrets(data, [("awx", "token"), ("external", "token")])
    assert out == {"awx": {}}


def test_secret_field_paths_matches_known_settings_secrets() -> None:
    # Pin the contract: every SecretStr in Settings is returned. Adding a
    # new SecretStr to the schema (per AGENTS.md "Recipe: add a new
    # setting") must make this test fail until the new path lands here.
    paths = secret_field_paths(get_settings_model())
    assert ("demo", "token") in paths
    assert len(paths) == 1  # Update when adding a new SecretStr to Settings.


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://bob:hunter2@proxy:8080", "http://bob:***@proxy:8080"),
        ("https://:tok@host/path", "https://:***@host/path"),
        ("http://bob@proxy:8080", "http://bob@proxy:8080"),
        ("http://proxy:8080", "http://proxy:8080"),
        ("not a url: a@b", "not a url: a@b"),
    ],
)
def test_redact_url_password_masks_only_the_password(value: str, expected: str) -> None:
    assert redact_url_password(value) == expected


def test_redact_secrets_masks_url_passwords_in_any_string_leaf() -> None:
    out = redact_secrets({"http": {"proxy": "http://u:p@h"}, "tags": ["https://u:p@h"]}, [])
    assert out == {"http": {"proxy": "http://u:***@h"}, "tags": ["https://u:***@h"]}
