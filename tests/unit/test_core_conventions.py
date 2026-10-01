"""The root commands and ``untaped.management`` follow the conventions."""

from __future__ import annotations

from untaped.conventions import core_violations


def test_core_follows_the_conventions() -> None:
    assert core_violations() == []
