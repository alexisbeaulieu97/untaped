"""Tests for the settings schema walker."""

from pathlib import Path
from typing import Annotated, Any

import pytest
from annotated_types import Ge, Gt, Le
from pydantic import BaseModel, Field, SecretStr

from untaped.config_schema import (
    FieldDescriptor,
    find_descriptor,
    redact_secrets,
    secret_field_paths,
    walk_settings,
)
from untaped.redaction import redact_url_password
from untaped.settings import (
    get_settings_model,
    register_profile_settings,
    register_state_settings,
)


class DemoProfileSettings(BaseModel):
    directory: Path = Path("~/.demo")
    token: SecretStr | None = None


class DemoStateSettings(BaseModel):
    entries: list[str] = Field(default_factory=list)


@pytest.fixture(autouse=True)
def _register_demo() -> None:
    register_profile_settings("demo", DemoProfileSettings)


def test_walks_nested_models() -> None:
    descriptors = walk_settings(get_settings_model())
    keys = {d.key for d in descriptors}
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


def test_descriptors_carry_the_fields_metadata_optionality_and_description() -> None:
    class Demo(BaseModel):
        parallel: int = Field(default=8, ge=1, le=32)
        page: Annotated[int, Gt(0)] = 5
        wrapped: Annotated[int, Ge(2)] | None = None
        workers: int | None = Field(default=None, ge=1, description="How many.")
        plain: str = "x"
        loud: bool | None = None

    parallel, page, wrapped, workers, plain, loud = walk_settings(Demo)

    assert parallel.metadata == (Ge(1), Le(32))
    assert page.metadata == (Gt(0),)
    assert wrapped.metadata == (Ge(2),)  # an Annotated inside the optional
    assert workers.metadata == (Ge(1),)
    assert plain.metadata == ()
    assert [d.optional for d in (parallel, page, wrapped, workers, plain, loud)] == [
        False, False, True, True, False, True
    ]  # fmt: skip
    assert workers.annotation is int  # the optional is still unwrapped
    assert [d.description for d in (parallel, workers, plain)] == [None, "How many.", None]


def test_a_descriptor_built_without_the_new_fields_keeps_working() -> None:
    descriptor = FieldDescriptor(
        path=("a", "b"), annotation=int, default=1, has_default=True, is_secret=False
    )

    assert (descriptor.metadata, descriptor.optional, descriptor.description) == ((), False, None)


def test_real_settings_keep_their_constraints() -> None:
    descriptors = walk_settings(get_settings_model())
    timeout = find_descriptor(descriptors, "http.timeout_seconds")
    hide = find_descriptor(descriptors, "ui.hide_empty_columns")

    assert timeout is not None
    assert timeout.metadata == (Gt(0),)
    assert not timeout.optional
    assert hide is not None
    assert hide.optional
    assert hide.annotation is bool


def test_secrets_are_marked() -> None:
    descriptors = walk_settings(get_settings_model())
    token = find_descriptor(descriptors, "demo.token")
    assert token is not None
    assert token.is_secret is True
    assert token.annotation is SecretStr

    updates = find_descriptor(descriptors, "skills.updates")
    assert updates is not None
    assert updates.is_secret is False


def test_defaults_are_captured() -> None:
    descriptors = walk_settings(get_settings_model())
    updates = find_descriptor(descriptors, "skills.updates")
    assert updates is not None
    assert updates.has_default
    assert updates.default == "warn"

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
    # new SecretStr to the schema (see CONTRIBUTING.md "Workflow")
    # must make this test fail until the new path lands here.
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
        (
            "HTTP 401 for https://u:s3cret@host/x and http://v:p@h",
            "HTTP 401 for https://u:***@host/x and http://v:***@h",
        ),
    ],
)
def test_redact_url_password_masks_only_the_password(value: str, expected: str) -> None:
    assert redact_url_password(value) == expected


def test_redact_secrets_masks_url_passwords_in_any_string_leaf() -> None:
    out = redact_secrets({"http": {"proxy": "http://u:p@h"}, "tags": ["https://u:p@h"]}, [])
    assert out == {"http": {"proxy": "http://u:***@h"}, "tags": ["https://u:***@h"]}
