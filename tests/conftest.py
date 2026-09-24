"""Suite-wide hermetic baseline shared by every capability's tests.

Tests must not see the developer machine: the real ``~/.untaped`` config,
ambient ``UNTAPED_*`` overrides, git's ``GIT_CONFIG_*`` injection, or the
terminal width all used to change results locally while CI masked them with
workflow-level environment variables. Capability conftests may layer more
specific fixtures on top of this one.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

import pytest

from untaped.prompts import reset_terminal_override, set_terminal_override
from untaped.settings import get_settings

_TERMINAL_ENV = {"TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "200"}


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Pin a machine-independent process environment for each test."""
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as patch:
        uv_cache = os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv")
        for key in list(os.environ):
            if key.startswith(("UNTAPED_", "GIT_CONFIG_")) or key == "GIT_CONFIG":
                patch.delenv(key)
        patch.setenv("HOME", str(home))
        patch.setenv("UV_CACHE_DIR", uv_cache)
        patch.setenv("UNTAPED_CONFIG", str(home / ".untaped" / "config.yml"))
        # ``UNTAPED_STATE`` was cleared above, so state.yml resolves next to
        # whichever temp config a test points ``UNTAPED_CONFIG`` at.
        assert "UNTAPED_STATE" not in os.environ
        patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for key, value in _TERMINAL_ENV.items():
            patch.setenv(key, value)
        get_settings.cache_clear()
        # No test may prompt on the developer's real terminal: the controlling
        # terminal is absent unless a test installs one (``invoke_cli(terminal=True)``).
        terminal_token = set_terminal_override(_no_controlling_terminal)
        try:
            yield
        finally:
            reset_terminal_override(terminal_token)
    get_settings.cache_clear()


def _no_controlling_terminal() -> TextIO:
    raise OSError("no controlling terminal in tests")
