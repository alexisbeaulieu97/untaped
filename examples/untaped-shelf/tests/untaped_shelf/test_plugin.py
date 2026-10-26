"""The example owner as a whole, tested the way any third-party plugin would be.

The owner and a provider together are tested in ``untaped-library``.
"""

from __future__ import annotations

from collections.abc import Callable

from untaped_shelf.plugin import Shelf

from untaped.sdk.testing import Install, assert_surface, check
from untaped_lint.testing import assert_clean

Installer = Callable[..., Install]


def test_shelf_passes_lint() -> None:
    assert_clean("shelf")


def test_shelf_passes_its_plugin_check(install: Installer) -> None:
    check(install(Shelf))


def test_shelf_keeps_its_surface() -> None:
    assert_surface(Shelf)  # snapshots/surface.txt beside this file
