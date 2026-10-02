"""Isolation for root management-surface tests (Wave 1.4)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from untaped import bootstrap


@pytest.fixture(autouse=True)
def _management_isolation() -> Iterator[None]:
    bootstrap._clear_for_tests()
    yield
    bootstrap._clear_for_tests()
