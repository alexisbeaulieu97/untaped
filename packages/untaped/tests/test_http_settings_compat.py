"""``HttpSettings.timeout`` stays a deprecated alias of ``timeout_seconds`` until 11.0."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from untaped.settings import HttpSettings, load_settings_section


def test_the_timeout_argument_still_works_with_a_deprecation_warning() -> None:
    with pytest.warns(DeprecationWarning, match="use timeout_seconds"):
        settings = HttpSettings(timeout=5)

    assert settings.timeout_seconds == 5
    with pytest.warns(DeprecationWarning, match="use timeout_seconds"):
        assert settings.timeout == 5


def test_timeout_seconds_wins_over_timeout() -> None:
    with pytest.warns(DeprecationWarning):
        settings = HttpSettings(timeout=5, timeout_seconds=7)

    assert settings.timeout_seconds == 7


def test_the_timeout_property_is_marked_deprecated() -> None:
    assert isinstance(HttpSettings.timeout, property)
    assert HttpSettings.timeout.fget.__deprecated__ == "use timeout_seconds"  # type: ignore[union-attr]


def test_http_timeout_in_config_is_read_as_timeout_seconds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"profiles": {"default": {"http": {"timeout": 9}}}}))

    assert load_settings_section("http").timeout_seconds == 9
    assert (
        "warning: http.timeout is deprecated and will be removed in the next major release; "
        "use http.timeout_seconds"
    ) in capsys.readouterr().err
