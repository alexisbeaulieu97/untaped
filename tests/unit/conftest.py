"""Shared unit-test fixtures for the untaped SDK."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped.identity import reset_tool_command
from untaped.settings import (
    get_settings,
    reset_config_registry_for_tests,
)


@pytest.fixture(autouse=True)
def _isolated_install_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests blind to the developer machine's real install state.

    Without this, anything touching the config file or the shared data dir
    reads the real ``~/.untaped/config.yml`` and ``~/.local/share/untaped``.
    Tests that need a config file still set ``UNTAPED_CONFIG`` themselves;
    this only provides a hermetic baseline.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "baseline-config.yml"))
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_tool_command() -> Iterator[None]:
    """Clear the process-global tool command so it never bleeds across tests."""
    reset_tool_command()
    yield
    reset_tool_command()


@pytest.fixture(autouse=True)
def _neutral_color_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ambient ``NO_COLOR``/``FORCE_COLOR`` so color assertions are stable.

    These are honoured at runtime (see ``untaped.render.should_colorize``), so a
    developer who exports ``FORCE_COLOR=3`` would otherwise leak color into the
    plain-stream tests. Clearing them here makes the suite deterministic without
    the ``env -u FORCE_COLOR`` wrapper; tests that exercise the env behaviour set
    the vars themselves via ``monkeypatch.setenv`` (which runs after this).
    """
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


@pytest.fixture
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    get_settings.cache_clear()
