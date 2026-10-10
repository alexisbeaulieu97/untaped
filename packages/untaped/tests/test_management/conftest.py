"""Isolation for root management-surface tests."""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from test_management import contract_plugins


@pytest.fixture(autouse=True)
def _management_isolation(fresh_composition: None) -> None:
    """Every management test starts and ends without a root composition."""


@pytest.fixture
def contract_plugins_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """``untaped_rack`` and ``untaped_bin`` on ``sys.path``; unloaded after the test."""
    site = tmp_path / "site"
    contract_plugins.write(site)
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    yield site
    for module in [m for m in sys.modules if m.split(".")[0] in contract_plugins.PACKAGES]:
        del sys.modules[module]
