"""Every contract test starts with fresh provider classes' counters."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core_test_support.contracts.support import fresh_providers


@pytest.fixture(autouse=True)
def _fresh_providers() -> Iterator[None]:
    with fresh_providers():
        yield
