"""Fixtures core's own plugin tests share: the contract plugins' site."""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from core_test_support.commands import contract_plugins


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
