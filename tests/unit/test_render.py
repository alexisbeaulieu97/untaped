"""Format-convention tests for the render module (ex-test_output.py).

These pin the cross-tool output conventions: json/yaml round-trip, raw
first-key + tab rules, dotted-column resolution, COLUMNS-driven table
width, Rich-markup safety, and the pipe envelope shape.
"""

import json
import os

import pytest
import yaml

from untaped.render import OutputFormat
from untaped.ui import UiContext


@pytest.fixture
def rows() -> list[dict[str, object]]:
    return [
        {"id": 1, "name": "alpha", "project_id": 100},
        {"id": 2, "name": "beta", "project_id": 200},
    ]


def _render(rows, **kwargs) -> str:
    return UiContext().collection(rows, **kwargs)


def test_json_format_round_trips(rows: list[dict[str, object]]) -> None:
    out = _render(rows, fmt="json")
    assert json.loads(out) == rows


def test_yaml_format_round_trips(rows: list[dict[str, object]]) -> None:
    out = _render(rows, fmt="yaml")
    assert yaml.safe_load(out) == rows


def test_raw_single_column_one_per_line(rows: list[dict[str, object]]) -> None:
    out = _render(rows, fmt="raw", columns=["name"])
    assert out.splitlines() == ["alpha", "beta"]


def test_raw_multi_column_tab_separated(rows: list[dict[str, object]]) -> None:
    out = _render(rows, fmt="raw", columns=["name", "project_id"])
    assert out.splitlines() == ["alpha\t100", "beta\t200"]


def test_raw_without_columns_picks_first_key(rows: list[dict[str, object]]) -> None:
    out = _render(rows, fmt="raw")
    assert out.splitlines() == ["1", "2"]


def test_table_format_returns_renderable_string(rows: list[dict[str, object]]) -> None:
    out = _render(rows, fmt="table")
    # Each row's name should appear somewhere in the rendered table.
    assert "alpha" in out
    assert "beta" in out


def test_unknown_format_raises() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        _render([], fmt="xml")  # type: ignore[arg-type]


def test_columns_filter_for_json(rows: list[dict[str, object]]) -> None:
    out = _render(rows, fmt="json", columns=["name"])
    assert json.loads(out) == [{"name": "alpha"}, {"name": "beta"}]


def test_output_format_literal_type() -> None:
    # Ensures OutputFormat is exported and usable for type annotation.
    fmt: OutputFormat = "json"
    assert fmt == "json"


@pytest.fixture
def nested_rows() -> list[dict[str, object]]:
    return [
        {
            "id": 1,
            "name": "alpha",
            "summary_fields": {
                "project": {"id": 10, "name": "playbooks"},
                "credentials": [
                    {"id": 30, "name": "ssh"},
                    {"id": 31, "name": "vault"},
                ],
            },
        },
        {
            "id": 2,
            "name": "beta",
            # Missing summary_fields entirely — must resolve to None, not error.
        },
    ]


def test_dotted_column_resolves_nested_value(
    nested_rows: list[dict[str, object]],
) -> None:
    out = _render(nested_rows, fmt="raw", columns=["name", "summary_fields.project.name"])
    assert out.splitlines() == ["alpha\tplaybooks", "beta\t"]


def test_dotted_column_in_json_uses_full_dotted_key(
    nested_rows: list[dict[str, object]],
) -> None:
    out = _render(nested_rows, fmt="json", columns=["summary_fields.project.name"])
    parsed = json.loads(out)
    assert parsed == [
        {"summary_fields.project.name": "playbooks"},
        {"summary_fields.project.name": None},
    ]


def test_dotted_column_resolves_for_table(
    nested_rows: list[dict[str, object]],
) -> None:
    """Table format must resolve dotted paths the same way raw/json/yaml do."""
    out = _render(nested_rows, fmt="table", columns=["name", "summary_fields.project.name"])
    # Resolved value present.
    assert "playbooks" in out
    # Column header is the full dotted path (lock in: not bare "name" twice
    # or just the last segment).
    assert "summary_fields.project.name" in out
    # Both rows render — the missing-summary row resolves to None, not error.
    assert "alpha" in out and "beta" in out


def test_scalar_list_renders_comma_separated_for_human_formats() -> None:
    rows = [{"name": "alpha", "credentials": ["ssh", "vault"]}]
    raw = _render(rows, fmt="raw", columns=["credentials"])
    table = _render(rows, fmt="table", columns=["credentials"])
    # splitlines() matches the rest of this file — robust to trailing newlines.
    assert raw.splitlines() == ["ssh, vault"]
    assert "ssh, vault" in table


def test_table_render_width_tracks_columns_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendered table width follows ``COLUMNS`` instead of a hard-coded value.

    Pins auto-detection: a regression to a fixed ``width=N`` would
    produce identical render widths under both env values and this
    test would fail.
    """
    rows = [{"name": "x" * 200}]

    def render_width(cols: str) -> int:
        monkeypatch.setenv("COLUMNS", cols)
        return max(len(line) for line in _render(rows, fmt="table").splitlines())

    assert render_width("60") <= 60
    assert render_width("240") >= 200


def test_table_render_preserves_bracketed_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Square brackets in user data are not interpreted as Rich markup.

    Regression guard: an AWX template named
    ``JOB-commun-gerer-acls-nonprod-[v2.3.1-test-aap]`` previously rendered
    in the ``--format table`` output as ``JOB-commun-gerer-acls-nonprod-``
    because Rich parsed ``[v2.3.1-test-aap]`` as a (malformed) markup tag
    and silently stripped it. Cells must be wrapped in ``rich.text.Text``
    (or otherwise have markup disabled) so bracketed user data survives
    rendering verbatim.
    """
    long_name = "JOB-commun-gerer-acls-nonprod-[v2.3.1-test-aap]"
    monkeypatch.setenv("COLUMNS", "200")
    out = _render([{"name": long_name}], fmt="table")
    assert long_name in out


def test_table_render_uses_detected_terminal_width_when_columns_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``COLUMNS`` is unset, table width follows the detected TTY size.

    Regression guard for the previous default: the Console wrote to a
    ``StringIO`` without an explicit ``width``, so Rich could not inspect
    the real TTY and fell back to its hard-coded 80 columns even in a
    wide terminal. ``shutil.get_terminal_size()`` is the standard way to
    pick up the actual size (or honour ``COLUMNS`` when it is set), so we
    pin that ``shutil`` is consulted rather than Rich's 80-col default.
    """
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.setattr(
        "shutil.get_terminal_size",
        lambda fallback=(80, 24): os.terminal_size((220, 50)),
    )
    rows = [{"name": "x" * 200}]
    width = max(len(line) for line in _render(rows, fmt="table").splitlines())
    assert width >= 200


def test_nested_list_falls_back_to_repr() -> None:
    """Lists of dicts are not flattened — they're structured data the
    user probably wants to inspect via json/yaml, not collapse."""
    rows = [{"name": "alpha", "items": [{"id": 1}, {"id": 2}]}]
    raw = _render(rows, fmt="raw", columns=["items"])
    assert "id" in raw
    assert "{" in raw  # repr-shaped, not "id, id"


def test_pipe_emits_one_self_describing_envelope_per_line(
    rows: list[dict[str, object]],
) -> None:
    out = _render(rows, fmt="pipe")
    lines = out.splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {
        "untaped": "1",
        "kind": None,
        "record": {"id": 1, "name": "alpha", "project_id": 100},
    }


def test_pipe_ignores_columns_and_emits_full_record(
    rows: list[dict[str, object]],
) -> None:
    out = _render(rows, fmt="pipe", columns=["name"])
    record = json.loads(out.splitlines()[0])["record"]
    assert record == {"id": 1, "name": "alpha", "project_id": 100}


def test_pipe_empty_rows_is_empty_string() -> None:
    assert _render([], fmt="pipe") == ""


def test_pipe_tags_kind_when_supplied(rows: list[dict[str, object]]) -> None:
    out = _render(rows, fmt="pipe", kind="awx.job_template")
    assert json.loads(out.splitlines()[0])["kind"] == "awx.job_template"
