"""Validation rules of the AWX settings."""

from __future__ import annotations

import os
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


def test_the_retired_test_timeout_is_not_read(capsys: pytest.CaptureFixture[str]) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"profiles": {"default": {"awx": {"test_timeout": 90.0}}}}))

    settings = get_config_section("awx", AwxSettings)

    assert settings.test_timeout_seconds == AwxSettings().test_timeout_seconds
    assert "test_timeout" not in capsys.readouterr().err
