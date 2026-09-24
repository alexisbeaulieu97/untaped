"""Unit tests for the ``patch`` field overlay (--set / --patch-file)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.awx.cli.patch_values import build_patch, parse_set_pairs
from untaped.capability_api import ConfigError

_RECORD = {"scm_branch": "main", "verbosity": 0, "extra_vars": "", "limit": None}


@pytest.mark.parametrize(
    ("pairs", "record", "expected"),
    [
        (
            ["verbosity=2", "enabled=true", "limit=null", "name=deploy", 'extra_vars={"a": 1}'],
            None,
            {"verbosity": 2, "enabled": True, "limit": None, "name": "deploy",
             "extra_vars": {"a": 1}},
        ),
        # values follow the record's existing field types; unknown fields are JSON-coerced
        (
            ["scm_branch=1.10", "verbosity=2", 'extra_vars={"a": 1}', "limit=null", "other=3"],
            _RECORD,
            {"scm_branch": "1.10", "verbosity": 2, "extra_vars": {"a": 1}, "limit": None,
             "other": 3},
        ),
        (["limit=a=b"], None, {"limit": "a=b"}),
        # a quoted number is a name, a bare one an id
        (['inventory="123"', "verbosity=4"], None, {"inventory": "123", "verbosity": 4}),
        (None, None, {}),
        ([], None, {}),
    ],
)  # fmt: skip
def test_parse_set_pairs(
    pairs: list[str] | None, record: dict[str, Any] | None, expected: dict[str, Any]
) -> None:
    assert parse_set_pairs(pairs, record=record) == expected


def test_parse_set_pairs_rejects_malformed() -> None:
    with pytest.raises(SystemExit):  # usage error, exit 2
        parse_set_pairs(["novalue"])


def test_build_patch_set_overrides_patch_file_under_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "p.yml").write_text("verbosity: 1\njob_tags: base\n")
    assert build_patch(["verbosity=5"], Path("~/p.yml")) == {"verbosity": 5, "job_tags": "base"}
    assert build_patch(["verbosity=2"], None) == {"verbosity": 2}


@pytest.mark.parametrize(
    ("content", "message"), [("- a\n- b\n", "must contain an object"), (None, "could not read")]
)
def test_build_patch_rejects_bad_patch_files(
    tmp_path: Path, content: str | None, message: str
) -> None:
    path = tmp_path / "p.yml"
    if content is not None:
        path.write_text(content)
    with pytest.raises(ConfigError, match=message):
        build_patch(None, path)
