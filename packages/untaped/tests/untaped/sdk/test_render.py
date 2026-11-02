"""Rendering rows: structured formats ignore the theme, and each pipe record's kind."""

import json
from pathlib import Path

import pytest

from untaped.runtime.settings import get_settings
from untaped.sdk._render import render_rows

# ---- render_rows -----------------------------------------------------------


def test_render_rows_structured_formats_ignore_theme(_isolated_config: Path) -> None:
    """json/raw output must stay byte-stable no matter the configured theme."""
    _isolated_config.write_text("untaped:\n  ui:\n    theme: dark\n")
    get_settings.cache_clear()

    rows: list[dict[str, object]] = [{"name": "alpha", "value": "1"}]
    assert json.loads(render_rows(rows, fmt="json")) == [{"name": "alpha", "value": "1"}]
    raw = render_rows(rows, fmt="raw", columns=["name"])
    assert raw == "alpha"
    assert "\x1b[" not in raw


def test_render_rows_pipe_tags_each_record_with_kind(_isolated_config: Path) -> None:
    rows: list[dict[str, object]] = [{"repo": "a/b"}, {"repo": "c/d"}]
    out = render_rows(rows, fmt="pipe", kind="github.repo")
    assert [json.loads(line) for line in out.splitlines()] == [
        {"untaped": "1", "kind": "github.repo", "record": {"repo": "a/b"}},
        {"untaped": "1", "kind": "github.repo", "record": {"repo": "c/d"}},
    ]


def test_render_rows_pipe_kind_defaults_to_null(_isolated_config: Path) -> None:
    out = render_rows([{"x": 1}], fmt="pipe")
    assert json.loads(out)["kind"] is None


@pytest.mark.parametrize(
    "kind",
    [
        "github.code_hit",
        "awx.apply_outcome",
        "profile.profile",
        "jira.issue.summary",
        "health.metric_source",
        "acme-tools.widget",
        "acme-tools.widget.summary",
    ],
)
def test_valid_kinds_are_accepted(kind: str) -> None:
    assert render_rows([{"id": 1}], fmt="pipe", kind=kind).startswith("{")


@pytest.mark.parametrize(
    "kind",
    [
        "github.codehit-x",
        "awx.apply-outcome",
        "Github.code_hit",
        "code_hit",
        "github.",
        "github.code_hit.extra",
        "github.code_hit.summary.x",
        "acme_tools.widget",
        "acme--tools.widget",
        "-acme.widget",
        "acme-.widget",
        "1acme.widget",
    ],
)
def test_invalid_kinds_raise_value_error(kind: str) -> None:
    with pytest.raises(ValueError, match="invalid record kind"):
        render_rows([{"id": 1}], fmt="pipe", kind=kind)


def test_summary_kind_is_reserved_as_suffix_only() -> None:
    with pytest.raises(ValueError, match="optionally followed by '\\.summary'"):
        render_rows([{"id": 1}], fmt="pipe", kind="github.summary")
