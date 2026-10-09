"""Every contract test starts with fresh provider classes' counters."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from test_contracts.support import Kiosk, Library, Shop
from untaped.contracts._registry import reset


@pytest.fixture(autouse=True)
def _fresh_providers() -> Iterator[None]:
    Library.calls = 0
    Library.error = None
    Shop.rows = []
    Shop.error = None
    Kiosk.rows = []
    reset()
    yield
    reset()
