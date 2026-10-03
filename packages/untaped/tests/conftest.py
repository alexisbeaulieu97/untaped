"""Shared unit-test fixtures."""

from __future__ import annotations

import pytest


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
