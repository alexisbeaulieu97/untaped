"""Shared unit-test fixtures, the `scripts/` loader, and first-party candidate helpers."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from pkgutil import resolve_name
from types import ModuleType

import pytest

from untaped.capabilities.registry import CapabilitySpec, ProviderCandidate, discover_candidates
from untaped.settings import (
    get_settings,
    reset_config_registry_for_tests,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name: str) -> ModuleType:
    """Import ``scripts/<name>.py`` as the module ``name`` (scripts are not a package)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@cache
def first_party_candidates() -> tuple[ProviderCandidate, ...]:
    """Every discovered first-party (distribution ``untaped``) candidate, in name order."""
    return tuple(
        sorted(
            (c for c in discover_candidates() if c.distribution == "untaped"),
            key=lambda c: c.name,
        )
    )


@cache
def first_party_specs() -> tuple[CapabilitySpec, ...]:
    """Every first-party spec, resolved from its discovered entry point, in name order."""
    return tuple(resolve_name(str(candidate.target))() for candidate in first_party_candidates())


def broken_first_party_candidates() -> tuple[ProviderCandidate, ...]:
    """First-party-looking ``awx`` and ``jira`` candidates whose entry points do not resolve."""
    return tuple(
        ProviderCandidate(distribution="untaped", name=name, target=f"untaped_missing_{name}:p")
        for name in ("awx", "jira")
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
    """Point the flattened config/profile stack at a temp config file."""
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    get_settings.cache_clear()
