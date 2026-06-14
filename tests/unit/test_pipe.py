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
    record: dict[str, object],
    *,
    kind: str | None = "github.repo",
    version: str = PIPE_ENVELOPE_VERSION,
) -> str:
    return json.dumps({PIPE_MARKER_KEY: version, "kind": kind, "record": record})


def test_is_envelope_line_true_for_marker_dict() -> None:
    assert is_envelope_line({"untaped": "1", "record": {}})


@pytest.mark.parametrize("obj", [123, "untaped", ["untaped"], {"kind": "x"}, None])
def test_is_envelope_line_false_for_non_marker(obj: object) -> None:
    assert not is_envelope_line(obj)


def test_parse_valid_line() -> None:
    env = parse_envelope_line(1, _line({"full_name": "a/b"}))
    assert env == PipeEnvelope(kind="github.repo", record={"full_name": "a/b"}, lineno=1)


def test_parse_null_kind() -> None:
    env = parse_envelope_line(2, _line({"x": 1}, kind=None))
    assert env.kind is None
    assert env.lineno == 2


def test_parse_invalid_json_is_line_precise() -> None:
    with pytest.raises(ConfigError, match="line 5: invalid JSON"):
        parse_envelope_line(5, "{not json")


def test_parse_non_envelope_is_line_precise() -> None:
    with pytest.raises(ConfigError, match="line 3: not an untaped pipe record"):
        parse_envelope_line(3, json.dumps({"record": {}}))


def test_parse_unsupported_version() -> None:
    with pytest.raises(ConfigError, match="line 1: unsupported pipe version '2'"):
        parse_envelope_line(1, _line({"x": 1}, version="2"))


def test_parse_record_not_object() -> None:
    bad = json.dumps({PIPE_MARKER_KEY: "1", "kind": None, "record": [1, 2]})
    with pytest.raises(ConfigError, match="line 4: record is not an object"):
        parse_envelope_line(4, bad)


def test_parse_kind_wrong_type() -> None:
    bad = json.dumps({PIPE_MARKER_KEY: "1", "kind": 5, "record": {}})
    with pytest.raises(ConfigError, match="line 1: kind must be a string or null"):
        parse_envelope_line(1, bad)


def test_common_kind_single() -> None:
    envs = [parse_envelope_line(i, _line({"x": i})) for i in (1, 2)]
    assert common_kind(envs) == "github.repo"


def test_common_kind_mixed_is_none() -> None:
    envs = [
        parse_envelope_line(1, _line({"x": 1}, kind="github.repo")),
        parse_envelope_line(2, _line({"x": 2}, kind="github.issue")),
    ]
    assert common_kind(envs) is None


def test_common_kind_empty_is_none() -> None:
    assert common_kind([]) is None


def test_common_kind_all_untagged_is_none() -> None:
    envs = [parse_envelope_line(i, _line({"x": i}, kind=None)) for i in (1, 2)]
    assert common_kind(envs) is None
