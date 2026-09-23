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

import pytest

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
        patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for key, value in _TERMINAL_ENV.items():
            patch.setenv(key, value)
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()
