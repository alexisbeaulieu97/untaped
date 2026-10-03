"""Validation rules of the AWX settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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
