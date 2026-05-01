import io
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from untaped_core.stdin import read_stdin


@pytest.fixture
def fake_stdin() -> Iterator[None]:
    with patch("sys.stdin") as mock:
        yield mock


def test_returns_empty_when_tty(fake_stdin: object) -> None:
    import sys

    sys.stdin.isatty.return_value = True  # type: ignore[attr-defined]
    assert read_stdin() == []


def test_reads_newline_separated_values() -> None:
    payload = "alpha\nbeta\n\ngamma\n"
    fake = io.StringIO(payload)
    fake.isatty = lambda: False  # type: ignore[method-assign]
    with patch("sys.stdin", fake):
        assert read_stdin() == ["alpha", "beta", "gamma"]


def test_strips_whitespace() -> None:
    fake = io.StringIO("  one  \n\ttwo\t\n")
    fake.isatty = lambda: False  # type: ignore[method-assign]
    with patch("sys.stdin", fake):
        assert read_stdin() == ["one", "two"]
