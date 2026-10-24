"""Pytest plugin: a hermetic environment for tests of untaped plugins.

Enable it with ``pytest_plugins = ["untaped.sdk.testing.plugin"]`` in a suite's
top-level ``conftest.py``. Every test then runs with its own ``HOME`` and
``UNTAPED_CONFIG``, no ``UNTAPED_*``/``GIT_CONFIG_*`` variables, no ambient
tokens (the variables every declared ``Credential`` reads among them), an
in-memory password store (:class:`untaped.sdk.testing.MemoryStore`), no
controlling terminal, plain non-colour output, and a fresh settings
registry. It is not auto-loaded, so installing untaped never changes
another project's tests.

Its fixtures: ``home`` (the hermetic home, :class:`~untaped.sdk.testing.Home`),
``install`` (:func:`untaped.sdk.testing.install`, composing plugin classes into
it) and ``clock`` (a :class:`~untaped.sdk.testing.ManualClock` every block
reads time from).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from untaped.runtime.settings import get_settings, reset_config_registry
from untaped.sdk.health.deprecated_keys import reset_key_warnings
from untaped.sdk.network.auth import clear_token_cache
from untaped.sdk.network.credentials import credential_env_names
from untaped.sdk.projects import _forget as _forget_projects
from untaped.sdk.prompt.prompts import reset_terminal_override, set_terminal_override
from untaped.sdk.storage.store import _forget_opened
from untaped.sdk.testing import (
    Home,
    ManualClock,
    manual_clock,
    memory_store,
    no_terminal,
)
from untaped.sdk.testing import (
    install as _install,
)

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
            ambient = key in _AMBIENT_ENV or key in credential_env_names()
            if key.startswith(("UNTAPED_", "GIT_CONFIG_")) or ambient:
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
            with memory_store():
                yield
        finally:
            reset_terminal_override(terminal_token)
            _forget_opened()  # the store layouts this test held lived under its HOME
            _forget_projects()  # and so did the projects it found
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_config_registry_for_tests() -> Iterator[None]:
    """Reset the registered config sections, and any loaded contract providers, around each test."""
    reset_config_registry()
    _forget_contract_providers()
    yield
    reset_config_registry()
    _forget_contract_providers()


def _forget_contract_providers() -> None:
    # Only when loaded: a test that asks no contract never imports untaped.sdk.contracts.
    registry = sys.modules.get("untaped.runtime.contracts.registry")
    if registry is not None:
        registry.reset()


@pytest.fixture
def home() -> Home:
    """The hermetic home this test runs in: ``home.path``, ``home.plugin(Hello)``."""
    return Home(Path.home())


@pytest.fixture
def install() -> Callable[..., Any]:
    """Compose plugin classes into the home: ``install(Hello, settings=HelloSettings(...))``."""
    return _install


@pytest.fixture
def clock() -> Iterator[ManualClock]:
    """The one clock blocks read: ``clock.advance(hours=7)`` ages caches and skips back-offs."""
    with manual_clock() as manual:
        yield manual
