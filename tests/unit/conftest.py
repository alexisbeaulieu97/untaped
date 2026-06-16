"""Shared unit-test fixtures for the untaped SDK."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from untaped.errors import ConfigError
from untaped.identity import reset_tool_command
from untaped.settings import (
    get_settings,
    register_settings_layout,
    reset_config_registry_for_tests,
)
from untaped.settings_layout import reset_flat_layout_warning_for_tests


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
def _reset_flat_layout_warning() -> Iterator[None]:
    """Keep the flat layout's warn-once latch from bleeding across tests."""
    reset_flat_layout_warning_for_tests()
    yield
    reset_flat_layout_warning_for_tests()


@pytest.fixture(autouse=True)
def _reset_tool_command() -> Iterator[None]:
    """Clear the process-global tool command so it never bleeds across tests."""
    reset_tool_command()
    yield
    reset_tool_command()


@pytest.fixture
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    get_settings.cache_clear()


class FakeScopedLayout:
    """A minimal scoped ``SettingsLayout`` for exercising core scope plumbing.

    Stores scopes under a ``profiles`` top-level key with a ``default``
    base layer beneath the ``active`` scope — mirroring the untaped-profile
    plugin's semantics closely enough to test core's pass-throughs
    (``--target-profile``, ``--all-profiles``, provenance with names)
    without depending on the plugin.
    """

    supports_scopes = True

    def effective(self, raw: dict[str, Any], *, scope: str | None = None) -> dict[str, Any]:
        name = scope or self._active(raw)
        merged = copy.deepcopy(self._profiles(raw).get("default", {}))
        if name != "default":
            _deep_merge(merged, self._profiles(raw).get(name, {}))
        return merged

    def provenance(self, raw: dict[str, Any]) -> dict[tuple[str, ...], str]:
        leaves: dict[tuple[str, ...], str] = {}
        layers = ["default"]
        active = self._active(raw)
        if active != "default":
            layers.append(active)
        for name in layers:
            for path, _ in _iter_leaves(self._profiles(raw).get(name, {}), ()):
                leaves[path] = name
        return leaves

    def scope_names(self, raw: dict[str, Any]) -> list[str]:
        return sorted(self._profiles(raw))

    def scope_data(self, raw: dict[str, Any], name: str) -> dict[str, Any] | None:
        data = self._profiles(raw).get(name)
        return data if isinstance(data, dict) else None

    def write_scope(
        self, raw: dict[str, Any], requested: str | None
    ) -> tuple[dict[str, Any], str | None]:
        name = requested or self._active(raw)
        known = self._profiles(raw)
        if name != "default" and name not in known:
            raise ConfigError(f"profile {name!r} does not exist")
        profiles = raw.setdefault("profiles", {})
        target = profiles.setdefault(name, {})
        if not isinstance(target, dict):
            raise ConfigError(f"profile {name!r} must be a mapping")
        return target, name

    @staticmethod
    def _profiles(raw: dict[str, Any]) -> dict[str, Any]:
        profiles = raw.get("profiles")
        return profiles if isinstance(profiles, dict) else {}

    @staticmethod
    def _active(raw: dict[str, Any]) -> str:
        active = raw.get("active")
        return active if isinstance(active, str) and active else "default"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def _iter_leaves(
    data: dict[str, Any], prefix: tuple[str, ...]
) -> list[tuple[tuple[str, ...], Any]]:
    out: list[tuple[tuple[str, ...], Any]] = []
    for key, value in data.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            out.extend(_iter_leaves(value, path))
        else:
            out.append((path, value))
    return out


@pytest.fixture
def fake_scoped_layout() -> FakeScopedLayout:
    """Register a scoped layout for the current test.

    Callers are responsible for resetting the config registry around the
    test (every settings-touching test file already does); registration is
    keyed so re-registration within one test stays legal.
    """
    layout = FakeScopedLayout()
    register_settings_layout(lambda: layout, key="test:fake-scoped")
    get_settings.cache_clear()
    return layout
