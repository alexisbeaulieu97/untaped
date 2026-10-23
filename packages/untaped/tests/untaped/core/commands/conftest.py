"""Isolation for root management-surface tests."""

import pytest


@pytest.fixture(autouse=True)
def _management_isolation(fresh_composition: None) -> None:
    """Every management test starts and ends without a root composition."""
