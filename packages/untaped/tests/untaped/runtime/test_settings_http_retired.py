"""``untaped.http.timeout`` and ``HttpSettings.timeout`` were retired in 11.0."""

from __future__ import annotations

import pytest
import yaml

from untaped.runtime.settings import HttpSettings, load_core_part
from untaped.sdk._home import config_path


def test_the_timeout_alias_is_gone() -> None:
    assert not hasattr(HttpSettings, "timeout")


def test_the_retired_http_timeout_is_not_read(capsys: pytest.CaptureFixture[str]) -> None:
    config = config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        yaml.safe_dump({"profiles": {"default": {"untaped": {"http": {"timeout": 9}}}}})
    )

    assert load_core_part("http").timeout_seconds == HttpSettings().timeout_seconds
    assert "untaped.http.timeout" not in capsys.readouterr().err


def test_the_timeout_argument_is_ignored() -> None:
    assert HttpSettings(timeout=5).timeout_seconds == HttpSettings().timeout_seconds  # type: ignore[call-arg]


def test_the_retired_variable_is_not_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("UNTAPED_UNTAPED__HTTP__TIMEOUT", "7")

    assert load_core_part("http").timeout_seconds == HttpSettings().timeout_seconds
    assert capsys.readouterr().err == ""
