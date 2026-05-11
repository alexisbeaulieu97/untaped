"""Tests for the settings schema walker."""

from pathlib import Path
from typing import Any

from pydantic import SecretStr
from untaped_core import (
    Settings,
    find_descriptor,
    redact_secrets,
    secret_field_paths,
    walk_settings,
)


def test_walks_nested_models() -> None:
    descriptors = walk_settings(Settings)
    keys = {d.key for d in descriptors}
    # Top-level scalar
    assert "log_level" in keys
    # HttpSettings
    assert "http.ca_bundle" in keys
    assert "http.verify_ssl" in keys
    # AwxSettings
    assert "awx.base_url" in keys
    assert "awx.token" in keys
    # GithubSettings
    assert "github.base_url" in keys
    assert "github.token" in keys


def test_skips_collection_fields() -> None:
    descriptors = walk_settings(Settings)
    keys = {d.key for d in descriptors}
    # ``workspace.workspaces`` is a list — should not appear. The sibling
    # scalar ``workspace.workspaces_dir`` must still appear, so a prefix
    # check would be too broad.
    assert "workspace.workspaces" not in keys
    assert "workspace.workspaces_dir" in keys


def test_secrets_are_marked() -> None:
    descriptors = walk_settings(Settings)
    awx_token = find_descriptor(descriptors, "awx.token")
    assert awx_token is not None
    assert awx_token.is_secret is True
    assert awx_token.annotation is SecretStr

    log_level = find_descriptor(descriptors, "log_level")
    assert log_level is not None
    assert log_level.is_secret is False


def test_defaults_are_captured() -> None:
    descriptors = walk_settings(Settings)
    log_level = find_descriptor(descriptors, "log_level")
    assert log_level is not None
    assert log_level.has_default
    assert log_level.default == "INFO"

    verify = find_descriptor(descriptors, "http.verify_ssl")
    assert verify is not None
    assert verify.default is True

    github_base = find_descriptor(descriptors, "github.base_url")
    assert github_base is not None
    assert github_base.default == "https://api.github.com"


def test_optional_unwrapped() -> None:
    descriptors = walk_settings(Settings)
    ca_bundle = find_descriptor(descriptors, "http.ca_bundle")
    assert ca_bundle is not None
    assert ca_bundle.annotation is Path


def test_find_descriptor_returns_none_for_unknown() -> None:
    descriptors = walk_settings(Settings)
    assert find_descriptor(descriptors, "does.not.exist") is None


def test_redact_secrets_replaces_secret_leaves() -> None:
    data: dict[str, Any] = {
        "awx": {"token": "xoxb-secret"},
        "github": {"token": "ghp_secret"},
    }
    out = redact_secrets(data, [("awx", "token"), ("github", "token")])
    assert out == {"awx": {"token": "***"}, "github": {"token": "***"}}
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
    out = redact_secrets(data, [("awx", "token"), ("github", "token")])
    assert out == {"awx": {}}


def test_secret_field_paths_matches_known_settings_secrets() -> None:
    # Pin the contract: every SecretStr in Settings is returned. Adding a
    # new SecretStr to the schema (per AGENTS.md "Recipe: add a new
    # setting") must make this test fail until the new path lands here.
    paths = secret_field_paths(Settings)
    assert ("awx", "token") in paths
    assert ("github", "token") in paths
    assert len(paths) == 2  # Update when adding a new SecretStr to Settings.
