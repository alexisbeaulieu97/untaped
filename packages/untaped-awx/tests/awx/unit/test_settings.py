"""Validation rules of the AWX settings."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from untaped.sdk import get_config_section
from untaped_awx.settings import AwxSettings


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("/api/v2/", "/api/v2/"),
        ("/api/v2", "/api/v2/"),
        ("/api/controller/v2//", "/api/controller/v2/"),
    ],
)
def test_api_prefix_gets_one_trailing_slash(given: str, stored: str) -> None:
    assert AwxSettings(api_prefix=given).api_prefix == stored


def test_api_prefix_must_start_with_a_slash() -> None:
    with pytest.raises(ValidationError, match="api_prefix"):
        AwxSettings(api_prefix="api/v2/")


@pytest.mark.parametrize(
    ("old", "value", "new", "attribute"),
    [
        ("test_timeout", 90.0, "test_timeout_seconds", lambda s: s.test_timeout_seconds),
    ],
)
def test_an_old_key_is_read_as_the_new_one(
    capsys: pytest.CaptureFixture[str],
    old: str,
    value: object,
    new: str,
    attribute: Callable[[AwxSettings], object],
) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {}
    node = data
    *parents, leaf = old.split(".")
    for part in parents:
        node = node.setdefault(part, {})  # type: ignore[assignment]
    node[leaf] = value
    config.write_text(yaml.safe_dump({"profiles": {"default": {"awx": data}}}))

    assert attribute(get_config_section("awx", AwxSettings)) == value
    assert (
        f"warning: awx.{old} is deprecated and will be removed in the next major release; "
        f"use awx.{new}"
    ) in capsys.readouterr().err
