import json

import pytest
import yaml
from untaped_core.output import OutputFormat, format_output


@pytest.fixture
def rows() -> list[dict[str, object]]:
    return [
        {"id": 1, "name": "alpha", "project_id": 100},
        {"id": 2, "name": "beta", "project_id": 200},
    ]


def test_json_format_round_trips(rows: list[dict[str, object]]) -> None:
    out = format_output(rows, fmt="json")
    assert json.loads(out) == rows


def test_yaml_format_round_trips(rows: list[dict[str, object]]) -> None:
    out = format_output(rows, fmt="yaml")
    assert yaml.safe_load(out) == rows


def test_raw_single_column_one_per_line(rows: list[dict[str, object]]) -> None:
    out = format_output(rows, fmt="raw", columns=["name"])
    assert out.splitlines() == ["alpha", "beta"]


def test_raw_multi_column_tab_separated(rows: list[dict[str, object]]) -> None:
    out = format_output(rows, fmt="raw", columns=["name", "project_id"])
    assert out.splitlines() == ["alpha\t100", "beta\t200"]


def test_raw_without_columns_picks_first_key(rows: list[dict[str, object]]) -> None:
    out = format_output(rows, fmt="raw")
    assert out.splitlines() == ["1", "2"]


def test_table_format_returns_renderable_string(rows: list[dict[str, object]]) -> None:
    out = format_output(rows, fmt="table")
    # Each row's name should appear somewhere in the rendered table.
    assert "alpha" in out
    assert "beta" in out


def test_unknown_format_raises() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        format_output([], fmt="xml")  # type: ignore[arg-type]


def test_columns_filter_for_json(rows: list[dict[str, object]]) -> None:
    out = format_output(rows, fmt="json", columns=["name"])
    assert json.loads(out) == [{"name": "alpha"}, {"name": "beta"}]


def test_format_literal_type() -> None:
    # Ensures OutputFormat is exported and usable for type annotation.
    fmt: OutputFormat = "json"
    assert fmt == "json"
