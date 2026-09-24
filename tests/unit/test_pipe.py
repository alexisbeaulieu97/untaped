"""Tests for the ``--format pipe`` wire contract (:mod:`untaped.pipe`)."""

from __future__ import annotations

import json

import pytest

from untaped.errors import ConfigError
from untaped.pipe import (
    PIPE_ENVELOPE_VERSION,
    PIPE_MARKER_KEY,
    PipeEnvelope,
    common_kind,
    is_envelope_line,
    parse_envelope_line,
)


def _line(
    record: object,
    *,
    kind: object = "github.repo",
    version: str = PIPE_ENVELOPE_VERSION,
) -> str:
    return json.dumps({PIPE_MARKER_KEY: version, "kind": kind, "record": record})


def test_is_envelope_line_true_for_marker_dict() -> None:
    assert is_envelope_line({"untaped": "1", "record": {}})


@pytest.mark.parametrize("obj", [123, "untaped", ["untaped"], {"kind": "x"}, None])
def test_is_envelope_line_false_for_non_marker(obj: object) -> None:
    assert not is_envelope_line(obj)


@pytest.mark.parametrize("kind", ["github.repo", None])
def test_parse_valid_line(kind: str | None) -> None:
    env = parse_envelope_line(2, _line({"full_name": "a/b"}, kind=kind))
    assert env == PipeEnvelope(kind=kind, record={"full_name": "a/b"}, lineno=2)


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ("{not json", "invalid JSON"),
        (json.dumps({"record": {}}), "not an untaped pipe record"),
        (_line({"x": 1}, version="2"), "unsupported pipe version '2'"),
        (_line([1, 2], kind=None), "record is not an object"),
        (_line({}, kind=5), "kind must be a string or null"),
    ],
)
def test_parse_errors_are_line_precise(line: str, error: str) -> None:
    with pytest.raises(ConfigError, match=f"line 4: {error}"):
        parse_envelope_line(4, line)


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        (["github.repo", "github.repo"], "github.repo"),
        (["github.repo", "github.issue"], None),
        ([None, None], None),
        ([], None),
    ],
    ids=["single", "mixed", "untagged", "empty"],
)
def test_common_kind(kinds: list[str | None], expected: str | None) -> None:
    envs = [parse_envelope_line(i, _line({"x": i}, kind=k)) for i, k in enumerate(kinds)]
    assert common_kind(envs) == expected
