"""``writes`` marks a command function as writing; the help-tree lint reads it."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from untaped.cli import write_kind
from untaped.sdk import writes


@pytest.mark.parametrize(
    ("decorate", "kind"),
    [(writes, "write"), (writes(destructive=True), "destructive")],
    ids=["bare", "destructive"],
)
def test_writes_marks_and_returns_the_same_function(
    decorate: Callable[[Callable[[], None]], Callable[[], None]], kind: str
) -> None:
    def command() -> None: ...

    assert decorate(command) is command
    assert write_kind(command) == kind


def test_undecorated_is_none() -> None:
    def list_command() -> None: ...

    assert write_kind(list_command) is None
