"""The example owner, tested the way any third-party plugin would be.

The owner and a provider together are tested in ``untaped-library``.
"""

from __future__ import annotations

from untaped.testing import check_conventions


def test_shelf_follows_the_conventions() -> None:
    check_conventions("shelf")
