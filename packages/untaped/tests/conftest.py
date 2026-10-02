"""Shared unit-test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.settings import get_settings


@pytest.fixture(autouse=True)
def _isolated_install_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests blind to the developer machine's shared data dir.

    Without this, anything touching the data dir reads the real
    ``~/.local/share/untaped``. The config file is isolated by the hermetic
    plugin.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    get_settings.cache_clear()


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
