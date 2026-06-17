"""Tests for the flat→profiles config migration warning.

The flat top-level config layout was removed in v1.0.1 (config now lives under
``profiles.default.<section>``). A registered profile section sitting at the top
level is silently ignored by the resolver, so loading warns instead of swallowing.
``http``/``ui`` legitimately stay top-level (state sections) and must not warn.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.settings import (
    get_settings,
    register_profile_settings,
    reset_config_registry_for_tests,
)


class DemoSettings(BaseModel):
    endpoint: str = "https://default.example"


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    reset_config_registry_for_tests()
    yield
    reset_config_registry_for_tests()


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_warns_when_registered_section_sits_at_top_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    register_profile_settings("demo", DemoSettings)
    cfg = tmp_path / "config.yml"
    cfg.write_text("demo:\n  endpoint: https://legacy.example\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="untaped"):
        get_settings()

    messages = _warnings(caplog)
    assert any("demo" in m and "profiles.default" in m for m in messages), messages


def test_no_warning_for_correctly_nested_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    register_profile_settings("demo", DemoSettings)
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    demo:\n      endpoint: https://ok.example\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="untaped"):
        get_settings()

    assert _warnings(caplog) == []


def test_warns_for_top_level_http_and_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # http/ui are per-profile in v2; a top-level block is silently ignored (and
    # `http.verify_ssl`/`proxy` affect security + connectivity), so it must warn.
    register_profile_settings("demo", DemoSettings)
    cfg = tmp_path / "config.yml"
    cfg.write_text("http:\n  verify_ssl: false\nui:\n  theme: plain\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="untaped"):
        get_settings()

    messages = _warnings(caplog)
    assert any("http" in m and "ui" in m and "profiles.default" in m for m in messages), messages


def test_warns_for_top_level_log_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # log_level is a base per-profile field too; flat at top level → ignored → warn.
    cfg = tmp_path / "config.yml"
    cfg.write_text("log_level: DEBUG\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="untaped"):
        get_settings()

    assert any("log_level" in m and "profiles.default" in m for m in _warnings(caplog))


def test_warns_at_most_once_per_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    register_profile_settings("demo", DemoSettings)
    cfg = tmp_path / "config.yml"
    cfg.write_text("demo:\n  endpoint: https://legacy.example\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING, logger="untaped"):
        get_settings()
        get_settings.cache_clear()  # force a second LayoutSettingsSource build
        get_settings()

    assert len([m for m in _warnings(caplog) if "demo" in m]) == 1
