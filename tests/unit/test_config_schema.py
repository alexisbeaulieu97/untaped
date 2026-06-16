"""Tests for the settings schema walker."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field, SecretStr

from untaped.config_schema import (
    find_descriptor,
    redact_secrets,
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
