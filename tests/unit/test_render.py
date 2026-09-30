"""Format-convention tests for the render module (ex-test_output.py).

These pin the cross-tool output conventions: json/yaml round-trip, raw
first-key + tab rules, dotted-column resolution, COLUMNS-driven table
width, Rich-markup safety, and the pipe envelope shape.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from untaped.theme import BUILTIN_THEMES, OutputFormat
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


@pytest.mark.parametrize("renderer", ["table", "list"])
def test_output_does_not_wrap_when_stdout_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch, renderer: str
) -> None:
    """Piped output (no terminal size, no ``COLUMNS``) is never wrapped at 80."""
    monkeypatch.delenv("COLUMNS", raising=False)

    def _no_terminal(*_args: object) -> os.terminal_size:
        raise OSError("not a terminal")

    monkeypatch.setattr("os.get_terminal_size", _no_terminal)
    if renderer == "list":
        monkeypatch.setenv("FORCE_COLOR", "1")  # styled record lines render through Rich
    rows = [{"name": "x" * 300, "description": "y " * 100}]
    theme = BUILTIN_THEMES["default" if renderer == "table" else "quiet"]
    lines = UiContext(theme=theme).collection(rows, fmt="table").splitlines()
    assert any("x" * 300 in line for line in lines)
    assert any(("y " * 100).strip() in line for line in lines)


def test_columns_still_bounds_the_width_when_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "60")
    out = _render([{"name": "x" * 300}], fmt="table")
    assert max(len(line) for line in out.splitlines()) <= 60


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


# ---- table cells --------------------------------------------------------------


def _table(rows: list[dict[str, object]], **kwargs: object) -> str:
    return UiContext().collection(rows, fmt="table", **kwargs)  # type: ignore[arg-type]


def test_table_columns_are_the_union_of_every_row() -> None:
    out = _table([{"name": "alpha"}, {"name": "beta", "detail": "boom"}])
    assert "detail" in out
    assert "boom" in out


def test_table_flattens_mappings_instead_of_printing_a_repr() -> None:
    out = _table([{"name": "h", "scope": {"parent": {"kind": "Inventory", "name": "prod"}}}])
    assert "parent.kind=Inventory, parent.name=prod" in out
    assert "{" not in out


def test_table_leaves_empty_containers_blank() -> None:
    out = _table([{"name": "a", "scope": {}, "tags": []}])
    assert "{}" not in out
    assert "[]" not in out


def test_table_renders_lists_of_mappings_readably() -> None:
    out = _table([{"name": "a", "items": [{"id": 1}, {"id": 2}]}])
    assert "id=1; id=2" in out


def test_table_collapses_multiline_text_to_one_line() -> None:
    out = _table([{"key": "A-1", "body": "first line\n\nsecond line"}])
    assert "first line second line" in out


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("duration_s", 42.13497729599476, "42.1s"),
        ("duration_s", 102.5, "1m42s"),
        ("duration_s", 3725.0, "1h02m"),
        ("load", 0.25, "0.25"),
        ("ratio", 0.123456, "0.12"),
        ("commit", "0123456789abcdef0123456789abcdef01234567", "0123456789"),
        ("scm_revision", "0123456789abcdef0123456789abcdef01234567", "0123456789"),
        ("name", "0123456789abcdef0123456789abcdef01234567", "0123456789abcdef"),
    ],
)
def test_table_formats_durations_floats_and_shas(key: str, value: object, expected: str) -> None:
    out = _table([{key: value}], columns=[key])
    assert expected in out
    full_sha = "0123456789abcdef0123456789abcdef01234567"
    assert (full_sha in out) == (key == "name")


def test_structured_formats_keep_values_verbatim() -> None:
    rows = [{"duration_s": 42.13497729599476, "scope": {}, "body": "a\nb"}]
    assert json.loads(_render(rows, fmt="json")) == rows


def test_wide_table_cells_end_in_an_ellipsis_instead_of_wrapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "60")
    out = _table([{"name": "alpha", "description": "word " * 40}])
    lines = out.splitlines()
    assert len(lines) == 5  # top border, header, rule, one row, bottom border
    assert "…" in out


def test_table_header_is_not_rich_markup() -> None:
    out = _table([{"[bold]x": 1}])
    assert "[bold]x" in out


def test_status_values_are_colored_by_meaning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    rows = [{"name": "a", "status": "failed"}, {"name": "b", "status": "successful"}]
    out = _table(rows)
    assert "\x1b[31mfailed" in out
    assert "\x1b[32msuccessful" in out
    assert "\x1b[31ma" not in out


def test_detail_view_flattens_nested_values() -> None:
    out = UiContext().detail({"name": "r", "inputs": [{"name": "a"}, {"name": "b"}]}, fmt="table")
    assert "inputs: name=a; name=b" in out


def test_detail_column_wraps_instead_of_being_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "60")
    out = _table([{"name": "alpha", "detail": "word " * 20 + "tail"}])
    assert "tail" in out
    assert "…" not in out


@pytest.mark.parametrize("fmt", ["json", "yaml", "raw", "table", "pipe"])
def test_datetimes_in_plain_rows_render_as_utc_timestamps(fmt: OutputFormat) -> None:
    stamp = datetime(2026, 1, 2, 4, 4, 5, 123456, tzinfo=timezone(timedelta(hours=1)))
    out = _render([{"created_at": stamp}], fmt=fmt)
    assert "2026-01-02T03:04:05Z" in out


def test_fitting_narrows_the_widest_column_before_names_and_short_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    rows = [
        {
            "name": "deploy-production-webservers",
            "action": "updated",
            "url": "https://example.com/" + "x" * 100,
        }
    ]
    out = _table(rows)
    assert "deploy-production-webservers" in out
    assert "updated" in out
    assert "https://example.com/x" in out
    assert max(len(line) for line in out.splitlines()) <= 80


def test_fitting_keeps_every_column_visible_when_the_terminal_is_narrow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "50")
    row = {"name": "n" * 30, "kind": "k" * 20, "scope": "s" * 20, "action": "a" * 20}
    header = _table([row]).splitlines()[1]
    assert all(col in header for col in row)


@pytest.mark.parametrize(("value", "expected"), [(5, "5.0s"), (59.96, "1m00s")])
def test_duration_edges(value: float, expected: str) -> None:
    assert expected in _table([{"wait_s": value}])


def test_detail_table_view_formats_each_field_by_name() -> None:
    theme = BUILTIN_THEMES["default"].model_copy(update={"detail_view": "table"})
    out = UiContext(theme=theme).detail({"duration_s": 102.5, "scope": {}}, fmt="table")
    assert "1m42s" in out
    assert "{}" not in out


def test_a_status_role_set_to_empty_turns_its_color_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    theme = BUILTIN_THEMES["default"].model_copy(update={"color_roles": {"error": ""}})
    out = UiContext(theme=theme).collection([{"status": "failed"}], fmt="table")
    assert "\x1b[31m" not in out
