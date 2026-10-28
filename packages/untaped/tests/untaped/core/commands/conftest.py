"""Isolation for root management-surface tests."""

from collections.abc import Iterator

import pytest

from core_test_support.runtime.run import holding_runs


@pytest.fixture(autouse=True)
def _management_isolation(fresh_composition: None) -> Iterator[None]:
    """Every management test starts and ends without a root composition or a held run."""
    with holding_runs():
        yield
