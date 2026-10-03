"""Pytest plugin: a hermetic environment for tests of untaped capabilities.

Enable it with ``pytest_plugins = ["untaped.testing.plugin"]`` in a suite's
top-level ``conftest.py``. Every test then runs with its own ``HOME`` and
``UNTAPED_CONFIG``, no ``UNTAPED_*``/``GIT_CONFIG_*`` variables, no ambient
tokens, no controlling terminal, plain non-colour output, and a fresh
settings registry. It is not auto-loaded, so installing untaped never changes
another project's tests.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped.auth import clear_token_cache
from untaped.deprecated_keys import reset_key_warnings
from untaped.prompts import reset_terminal_override, set_terminal_override
from untaped.settings import get_settings, reset_config_registry_for_tests
from untaped.testing import no_terminal

_TERMINAL_ENV = {"TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "200"}
# Ambient token fallbacks (``GH_TOKEN``) would otherwise leak a real token in.
_AMBIENT_ENV = frozenset(
    {
        "GIT_CONFIG",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "JIRA_API_TOKEN",
        "CONTROLLER_OAUTH_TOKEN",
        "TOWER_OAUTH_TOKEN",
        "AAP_TOKEN",
    }
)


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Pin a machine-independent process environment for each test."""
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as patch:
        uv_cache = os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv")
        for key in list(os.environ):
            if key.startswith(("UNTAPED_", "GIT_CONFIG_")) or key in _AMBIENT_ENV:
                patch.delenv(key)
        patch.setenv("HOME", str(home))
        patch.setenv("UV_CACHE_DIR", uv_cache)
        # A fresh HOME hides uv's managed interpreters. Use the test runner's
        # Python for real hook workers instead of downloading it for each test.
        patch.setenv("UV_PYTHON", sys.executable)
        patch.setenv("UV_PYTHON_DOWNLOADS", "never")
        patch.setenv("UNTAPED_CONFIG", str(home / ".untaped" / "config.yml"))
        # ``UNTAPED_STATE`` was cleared above, so state.yml resolves next to
        # whichever temp config a test points ``UNTAPED_CONFIG`` at.
        patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for key, value in _TERMINAL_ENV.items():
            patch.setenv(key, value)
        get_settings.cache_clear()
        clear_token_cache()
        reset_key_warnings()
        # No test may prompt on the developer's real terminal: the controlling
        # terminal is absent unless a test installs one (``invoke_cli(terminal=True)``).
        terminal_token = set_terminal_override(no_terminal)
        try:
            yield
        finally:
            reset_terminal_override(terminal_token)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_config_registry_for_tests() -> Iterator[None]:
    """Reset the registered config sections around each test."""
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    get_settings.cache_clear()
