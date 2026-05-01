"""Tests for the ``read_identifiers`` helper that pipes/positional commands share."""

from __future__ import annotations

import io

import pytest
from untaped_core import ConfigError
from untaped_core.stdin import read_identifiers


def test_returns_positional_when_stdin_flag_off() -> None:
    assert read_identifiers(["a", "b"], stdin=False) == ["a", "b"]


def test_returns_stdin_lines_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("a\nb\n c \n"))
    assert read_identifiers([], stdin=True) == ["a", "b", "c"]


def test_rejects_both_positional_and_stdin() -> None:
    """Mixing sources is almost always a user error — refuse so a typo
    can't silently act on a partial set of identifiers."""
    with pytest.raises(ConfigError, match=r"both"):
        read_identifiers(["a"], stdin=True)


def test_rejects_empty_positional_when_stdin_off() -> None:
    """Without --stdin, at least one positional is required so the command
    doesn't no-op when given no work to do."""
    with pytest.raises(ConfigError, match=r"at least one"):
        read_identifiers([], stdin=False)


def test_rejects_empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--stdin`` with no piped lines is also an error — same reason as
    empty positional: avoid silent no-op when the upstream pipe ran dry."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(ConfigError, match=r"stdin"):
        read_identifiers([], stdin=True)
