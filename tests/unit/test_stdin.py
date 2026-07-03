import io
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from untaped.errors import ConfigError
from untaped.pipe import PIPE_MARKER_KEY
from untaped.stdin import (
    read_identifiers,
    read_records,
    read_stdin,
    read_stdin_text,
    resolve_text_input,
)


def _feed(payload: str) -> object:
    fake = io.StringIO(payload)
    fake.isatty = lambda: False  # type: ignore[method-assign]
    return patch("sys.stdin", fake)


def _env(record: dict[str, object], kind: str | None = "github.repo") -> str:
    return json.dumps({PIPE_MARKER_KEY: "1", "kind": kind, "record": record})


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


# ---- read_records ----------------------------------------------------------


def test_read_records_parses_envelopes() -> None:
    payload = _env({"full_name": "a/b"}) + "\n" + _env({"full_name": "c/d"}) + "\n"
    with _feed(payload):
        envs = read_records()
    assert [e.record["full_name"] for e in envs] == ["a/b", "c/d"]
    assert [e.kind for e in envs] == ["github.repo", "github.repo"]


def test_read_records_empty_raises() -> None:
    with _feed(""), pytest.raises(ConfigError, match="no records received on stdin"):
        read_records()


def test_read_records_malformed_line_is_line_precise() -> None:
    payload = _env({"x": 1}) + "\nnot json\n"
    with _feed(payload), pytest.raises(ConfigError, match="line 2: invalid JSON"):
        read_records()


def test_read_records_non_envelope_line_raises() -> None:
    with (
        _feed('{"foo": 1}\n'),
        pytest.raises(ConfigError, match="line 1: not an untaped pipe record"),
    ):
        read_records()


# ---- read_identifiers: envelope mode ---------------------------------------


def test_read_identifiers_envelope_mode_extracts_id_field() -> None:
    payload = _env({"full_name": "a/b"}) + "\n" + _env({"full_name": "c/d"}) + "\n"
    with _feed(payload):
        assert read_identifiers([], stdin=True, id_field="full_name") == ["a/b", "c/d"]


def test_read_identifiers_envelope_requires_id_field() -> None:
    with _feed(_env({"full_name": "a/b"}) + "\n"), pytest.raises(ConfigError, match="pipe format"):
        read_identifiers([], stdin=True)


def test_read_identifiers_envelope_missing_field_is_line_precise() -> None:
    payload = _env({"full_name": "a/b"}) + "\n" + _env({"other": "x"}) + "\n"
    with (
        _feed(payload),
        pytest.raises(ConfigError, match="line 2: record 'full_name' is missing or null"),
    ):
        read_identifiers([], stdin=True, id_field="full_name")


def test_read_identifiers_envelope_blank_field_rejected() -> None:
    with (
        _feed(_env({"full_name": "   "}) + "\n"),
        pytest.raises(ConfigError, match="line 1: record 'full_name' is blank"),
    ):
        read_identifiers([], stdin=True, id_field="full_name")


def test_read_identifiers_envelope_coerces_non_string_id() -> None:
    with _feed(_env({"id": 123}) + "\n"):
        assert read_identifiers([], stdin=True, id_field="id") == ["123"]


# ---- read_identifiers: bare mode, back-compat, detection -------------------


def test_read_identifiers_bare_lines_backward_compat() -> None:
    with _feed("a/b\nc/d\n"):
        assert read_identifiers([], stdin=True) == ["a/b", "c/d"]


def test_read_identifiers_bare_scalar_json_is_not_envelope() -> None:
    # 123 (int) and "untaped" (str) parse as JSON but aren't marker-dicts → bare.
    with _feed('123\n"untaped"\n'):
        assert read_identifiers([], stdin=True) == ["123", '"untaped"']


def test_read_identifiers_mixed_bare_then_envelope_raises() -> None:
    payload = "a/b\n" + _env({"full_name": "c/d"}) + "\n"
    with _feed(payload), pytest.raises(ConfigError, match="mixed bare/envelope input on stdin"):
        read_identifiers([], stdin=True, id_field="full_name")


# ---- raw text input --------------------------------------------------------


def test_read_stdin_text_preserves_interior_newlines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("line one\n\nline three\n"))
    assert read_stdin_text() == "line one\n\nline three"


def test_read_stdin_text_trims_exactly_one_trailing_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("x\n\n"))
    assert read_stdin_text() == "x\n"


def test_read_stdin_text_handles_crlf_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("x\r\n"))
    assert read_stdin_text() == "x"


def test_read_stdin_text_is_empty_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    from untaped.testing import TtyStringIO

    monkeypatch.setattr("sys.stdin", TtyStringIO("never read"))
    assert read_stdin_text() == ""


def test_resolve_text_input_flag_wins(tmp_path: Path) -> None:
    assert resolve_text_input(value="inline", file=None) == "inline"


def test_resolve_text_input_sources_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        resolve_text_input("inline", None)


def test_resolve_text_input_empty_flag_is_config_error() -> None:
    with pytest.raises(ConfigError, match="no body provided"):
        resolve_text_input(value="", file=None)


def test_resolve_text_input_rejects_flag_plus_file(tmp_path: Path) -> None:
    f = tmp_path / "body.txt"
    f.write_text("filed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not both"):
        resolve_text_input(value="inline", file=f)


def test_resolve_text_input_reads_file_trimming_terminal_newline(tmp_path: Path) -> None:
    f = tmp_path / "body.txt"
    f.write_text("from file\n", encoding="utf-8")
    assert resolve_text_input(value=None, file=f) == "from file"


def test_resolve_text_input_empty_file_is_config_error(tmp_path: Path) -> None:
    f = tmp_path / "body.txt"
    f.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="no body provided"):
        resolve_text_input(value=None, file=f)


def test_resolve_text_input_reads_file_newlines_verbatim(tmp_path: Path) -> None:
    f = tmp_path / "body.txt"
    f.write_bytes(b"one\r\ntwo\r\n")
    assert resolve_text_input(value=None, file=f) == "one\r\ntwo"


def test_resolve_text_input_falls_back_to_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("piped body\n"))
    assert resolve_text_input(value=None, file=None) == "piped body"


def test_resolve_text_input_empty_everything_is_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from untaped.testing import TtyStringIO

    monkeypatch.setattr("sys.stdin", TtyStringIO())
    with pytest.raises(ConfigError, match="no body provided"):
        resolve_text_input(value=None, file=None)
