"""Tests for the settings schema walker."""

from pathlib import Path

from pydantic import SecretStr
from untaped_core.config_schema import find_descriptor, walk_settings
from untaped_core.settings import Settings


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
    # ``workspace.workspaces`` is a list — should not appear
    assert not any(k.startswith("workspace.workspaces") for k in keys)


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
