"""``writes`` marks a command function as writing; the help-tree lint reads it."""

from __future__ import annotations

from untaped.cli import write_kind
from untaped.sdk import writes


def test_bare_decorator_marks_a_write() -> None:
    @writes
    def set_command() -> None: ...

    assert write_kind(set_command) == "write"


def test_destructive_flag_marks_destructive() -> None:
    @writes(destructive=True)
    def purge_command() -> None: ...

    assert write_kind(purge_command) == "destructive"


def test_undecorated_is_none() -> None:
    def list_command() -> None: ...

    assert write_kind(list_command) is None


def test_bare_decorator_returns_the_same_function() -> None:
    def f() -> None: ...

    assert writes(f) is f
    assert write_kind(f) == "write"


def test_called_decorator_returns_the_same_function() -> None:
    def g() -> None: ...

    assert writes(destructive=True)(g) is g
    assert write_kind(g) == "destructive"
