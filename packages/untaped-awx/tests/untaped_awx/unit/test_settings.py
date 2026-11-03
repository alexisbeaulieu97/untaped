"""Validation rules of the AWX settings."""

from __future__ import annotations

import pytest
import yaml

from untaped.sdk import home, settings_of
from untaped.sdk.testing import install
from untaped_awx.plugin import Awx
from untaped_awx.settings import AwxSettings


def test_the_retired_test_timeout_is_not_read(capsys: pytest.CaptureFixture[str]) -> None:
    installed = install(Awx)  # before the config it reads: install writes its sections
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"format_version": 2, "awx": {"test_timeout": 90.0}}))

    with installed.running():
        settings = settings_of(AwxSettings)

    assert settings.test_timeout_seconds == AwxSettings().test_timeout_seconds
    assert "test_timeout" not in capsys.readouterr().err


def test_the_retired_base_url_is_not_read(capsys: pytest.CaptureFixture[str]) -> None:
    installed = install(Awx)  # before the config it reads: install writes its sections
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        yaml.safe_dump({"format_version": 2, "awx": {"base_url": "https://aap.example.com"}})
    )

    with installed.running():
        settings = settings_of(AwxSettings)

    assert settings.server is None
    assert "base_url" not in capsys.readouterr().err
