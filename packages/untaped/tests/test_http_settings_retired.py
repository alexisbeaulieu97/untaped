"""``http.timeout`` and ``HttpSettings.timeout`` were retired in 11.0."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from untaped.settings import HttpSettings, load_settings_section


def test_the_timeout_alias_is_gone() -> None:
    assert not hasattr(HttpSettings, "timeout")


def test_the_retired_http_timeout_is_not_read(capsys: pytest.CaptureFixture[str]) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"profiles": {"default": {"http": {"timeout": 9}}}}))

    assert load_settings_section("http").timeout_seconds == HttpSettings().timeout_seconds
    assert "http.timeout" not in capsys.readouterr().err
