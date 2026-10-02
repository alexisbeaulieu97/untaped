"""Process-scoped ``--quiet`` state (the inverse of ``--verbose``)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from untaped.quiet import enable, is_quiet, reset


@pytest.fixture(autouse=True)
def _reset_quiet() -> Iterator[None]:
    reset()
    yield
    reset()


def test_quiet_is_disabled_by_default() -> None:
    assert is_quiet() is False


def test_enable_turns_quiet_on() -> None:
    enable()
    assert is_quiet() is True


def test_reset_clears_quiet() -> None:
    enable()
    reset()
    assert is_quiet() is False
