import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from untaped.errors import ConfigError, UsageError
from untaped.pipe import PIPE_MARKER_KEY
from untaped.stdin import (
    StdinInput,
    read_identifiers,
    read_records,
    read_stdin,
    read_stdin_input,
    read_stdin_text,
    resolve_text_input,
)
from untaped.testing import TtyStringIO


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


# ---- read_records ----------------------------------------------------------


def test_read_records_parses_envelopes() -> None:
    payload = _env({"full_name": "a/b"}) + "\n" + _env({"full_name": "c/d"}) + "\n"
    with _feed(payload):
        envs = read_records()
    assert [e.record["full_name"] for e in envs] == ["a/b", "c/d"]
    assert [e.kind for e in envs] == ["github.repo", "github.repo"]


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ("", "no records received on stdin"),
        (_env({"x": 1}) + "\nnot json\n", "line 2: invalid JSON"),
        ('{"foo": 1}\n', "line 1: not an untaped pipe record"),
    ],
)
def test_read_records_errors(payload: str, error: str) -> None:
    with _feed(payload), pytest.raises(ConfigError, match=error):
        read_records()


# ---- read_identifiers ------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "id_field", "expected"),
    [
        (
            _env({"full_name": "a/b"}) + "\n" + _env({"full_name": "c/d"}),
            "full_name",
            ["a/b", "c/d"],
        ),
        (_env({"id": 123}), "id", ["123"]),
        ("a/b\nc/d\n", None, ["a/b", "c/d"]),
        # 123 (int) and "untaped" (str) parse as JSON but aren't marker-dicts → bare.
        ('123\n"untaped"\n', None, ["123", '"untaped"']),
    ],
    ids=["envelope", "envelope-non-string-id", "bare", "bare-scalar-json"],
)
def test_read_identifiers(payload: str, id_field: str | None, expected: list[str]) -> None:
    with _feed(payload + "\n"):
        assert read_identifiers([], stdin=True, id_field=id_field) == expected


@pytest.mark.parametrize(
    ("payload", "id_field", "error"),
    [
        (_env({"full_name": "a/b"}), None, "pipe format"),
        (
            _env({"full_name": "a/b"}) + "\n" + _env({"other": "x"}),
            "full_name",
            "line 2: record 'full_name' is missing or null",
        ),
        (_env({"full_name": "   "}), "full_name", "line 1: record 'full_name' is blank"),
        ("a/b\n" + _env({"full_name": "c/d"}), "full_name", "mixed bare/envelope input on stdin"),
    ],
    ids=["no-id-field", "missing-field", "blank-field", "mixed"],
)
def test_read_identifiers_errors(payload: str, id_field: str | None, error: str) -> None:
    with _feed(payload + "\n"), pytest.raises(ConfigError, match=error):
        read_identifiers([], stdin=True, id_field=id_field)


# ---- accepted kinds ---------------------------------------------------------


def test_read_identifiers_accepts_declared_kinds_and_kindless_records() -> None:
    payload = _env({"id": 1}, kind="awx.job") + "\n" + _env({"id": 2}, kind=None) + "\n"
    with _feed(payload):
        assert read_identifiers([], stdin=True, id_field="id", accept_kinds={"awx.job"}) == [
            "1",
            "2",
        ]


def test_read_identifiers_rejects_other_kinds_as_usage_error() -> None:
    payload = _env({"id": 1}, kind="awx.job") + "\n" + _env({"id": 7}, kind="awx.host") + "\n"
    with (
        _feed(payload),
        pytest.raises(
            UsageError,
            match=r"line 2: record kind 'awx\.host' is not accepted here; expected 'awx\.job'",
        ),
    ):
        read_identifiers([], stdin=True, id_field="id", accept_kinds={"awx.job"})


def test_read_records_checks_accepted_kinds() -> None:
    with _feed(_env({"id": 1}, kind="github.repo") + "\n"), pytest.raises(UsageError):
        read_records(accept_kinds={"workspace.repo"})


def test_read_stdin_input_returns_bare_values_or_records() -> None:
    with _feed("a\nb\n"):
        bare = read_stdin_input()
    with _feed(_env({"id": 1}) + "\n"):
        piped = read_stdin_input(accept_kinds={"github.repo"})

    assert bare == StdinInput(values=("a", "b"), records=None)
    assert piped.values == ()
    assert piped.records is not None and piped.records[0].record == {"id": 1}


def test_read_stdin_input_names_what_was_expected_when_empty() -> None:
    with _feed(""), pytest.raises(ConfigError, match="no names received on stdin"):
        read_stdin_input(what="names")


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


def test_resolve_text_input_flag_wins() -> None:
    assert resolve_text_input(value="inline", file=None) == "inline"


def test_resolve_text_input_rejects_flag_plus_file(tmp_path: Path) -> None:
    f = tmp_path / "body.txt"
    f.write_text("filed", encoding="utf-8")
    with pytest.raises(UsageError, match="not both"):
        resolve_text_input(value="inline", file=f)


@pytest.mark.parametrize(
    ("content", "expected"),
    [(b"from file\n", "from file"), (b"one\r\ntwo\r\n", "one\r\ntwo")],
    ids=["trims-terminal-newline", "newlines-verbatim"],
)
def test_resolve_text_input_reads_file(tmp_path: Path, content: bytes, expected: str) -> None:
    f = tmp_path / "body.txt"
    f.write_bytes(content)
    assert resolve_text_input(value=None, file=f) == expected


def test_resolve_text_input_falls_back_to_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("piped body\n"))
    assert resolve_text_input(value=None, file=None) == "piped body"


@pytest.mark.parametrize("source", ["flag", "file", "stdin"])
def test_resolve_text_input_empty_body_is_config_error(
    source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "body.txt"
    f.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", TtyStringIO())
    with pytest.raises(ConfigError, match="no body provided"):
        resolve_text_input(
            value="" if source == "flag" else None, file=f if source == "file" else None
        )
