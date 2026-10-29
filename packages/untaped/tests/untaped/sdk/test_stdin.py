import io
import json
from unittest.mock import patch

import pytest

from untaped.sdk._pipe import PIPE_MARKER_KEY
from untaped.sdk._stdin import (
    StdinInput,
    read_stdin,
    read_stdin_input,
    read_stdin_line,
    read_stdin_text,
)
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.testing import TtyStringIO


def _feed(payload: str) -> object:
    fake = io.StringIO(payload)
    fake.isatty = lambda: False  # type: ignore[method-assign]
    return patch("sys.stdin", fake)


def _env(record: dict[str, object], kind: str | None = "github.repo") -> str:
    return json.dumps({PIPE_MARKER_KEY: "1", "kind": kind, "record": record})


def test_read_stdin_is_empty_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", TtyStringIO("never read"))
    assert read_stdin() == []


def test_read_stdin_strips_and_skips_blank_lines() -> None:
    with _feed("  alpha  \n\tbeta\t\n\ngamma\n"):
        assert read_stdin() == ["alpha", "beta", "gamma"]


# ---- pipe streams ----------------------------------------------------------


def test_read_stdin_input_parses_envelopes() -> None:
    payload = _env({"full_name": "a/b"}) + "\n" + _env({"full_name": "c/d"}) + "\n"
    with _feed(payload):
        records = read_stdin_input().records
    assert records is not None
    assert [e.record["full_name"] for e in records] == ["a/b", "c/d"]
    assert [e.kind for e in records] == ["github.repo", "github.repo"]


def test_read_stdin_input_names_the_malformed_envelope_line() -> None:
    with (
        _feed(_env({"x": 1}) + "\nnot json\n"),
        pytest.raises(ConfigError, match="line 2: invalid JSON"),
    ):
        read_stdin_input()


def test_read_stdin_input_checks_accepted_kinds() -> None:
    with _feed(_env({"id": 1}, kind="github.repo") + "\n"), pytest.raises(UsageError):
        read_stdin_input(accept_kinds={"workspace.repo"})


def test_another_kind_is_refused_naming_the_accepted_kinds() -> None:
    with (
        _feed(_env({"id": 7}, kind="forge.host") + "\n"),
        pytest.raises(
            UsageError,
            match=(
                r"line 1: record kind 'forge\.host' is not accepted here; "
                r"expected 'forge\.run', 'forge\.run_outcome'"
            ),
        ),
    ):
        read_stdin_input(accept_kinds={"forge.run_outcome", "forge.run"})


def test_read_stdin_input_returns_bare_values_or_records() -> None:
    with _feed("a\nb\n"):
        bare = read_stdin_input()
    with _feed(_env({"id": 1}) + "\n"):
        piped = read_stdin_input(accept_kinds={"github.repo"})

    assert bare == StdinInput(values=("a", "b"), records=None)
    assert piped.values == ()
    assert piped.records is not None and piped.records[0].record == {"id": 1}


def test_read_stdin_input_refuses_an_empty_pipe() -> None:
    with _feed(""), pytest.raises(ConfigError, match="nothing received on stdin"):
        read_stdin_input()


@pytest.mark.parametrize("payload", ["", "\n  \n"], ids=["empty", "blank-lines"])
def test_allow_empty_turns_an_empty_pipe_into_no_values(payload: str) -> None:
    with _feed(payload):
        assert read_stdin_input(allow_empty=True) == StdinInput(values=(), records=None)


def test_allow_empty_still_refuses_a_terminal_with_nothing_piped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin", TtyStringIO("never read"))
    with pytest.raises(ConfigError, match="nothing received on stdin"):
        read_stdin_input(allow_empty=True)


# ---- raw text input --------------------------------------------------------


@pytest.mark.parametrize(
    ("stdin", "expected"),
    [
        (io.StringIO("line one\n\nline three\n"), "line one\n\nline three"),
        # Exactly one trailing newline (LF or CRLF) is trimmed.
        (io.StringIO("x\n\n"), "x\n"),
        (io.StringIO("x\r\n"), "x"),
        (TtyStringIO("never read"), ""),
    ],
    ids=["interior-newlines", "one-trailing-newline", "crlf", "tty"],
)
def test_read_stdin_text(
    stdin: io.StringIO, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", stdin)
    assert read_stdin_text() == expected


def test_read_stdin_line_keeps_the_line_as_typed() -> None:
    with _feed(" p4ss #word \r\n"):
        assert read_stdin_line("token") == " p4ss #word "


@pytest.mark.parametrize(
    ("stdin", "message"),
    [
        (TtyStringIO("never read"), "no token received on stdin"),
        (io.StringIO(" \n"), "no token received on stdin"),
        (io.StringIO("a\nb\n"), "stdin should hold the token on one line"),
        (io.StringIO("a\rb"), "stdin should hold the token on one line"),
    ],
    ids=["tty", "blank", "two-lines", "carriage-return"],
)
def test_read_stdin_line_refuses_nothing_or_more_than_one_line(
    stdin: io.StringIO, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", stdin)
    with pytest.raises(ConfigError, match=message) as caught:
        read_stdin_line("token")
    assert caught.value.category == "invalid"
