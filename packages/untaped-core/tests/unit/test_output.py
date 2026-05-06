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
    out = format_output(nested_rows, fmt="raw", columns=["name", "summary_fields.project.name"])
    assert out.splitlines() == ["alpha\tplaybooks", "beta\t"]


def test_dotted_column_in_json_uses_full_dotted_key(
    nested_rows: list[dict[str, object]],
) -> None:
    out = format_output(nested_rows, fmt="json", columns=["summary_fields.project.name"])
    parsed = json.loads(out)
    assert parsed == [
        {"summary_fields.project.name": "playbooks"},
        {"summary_fields.project.name": None},
    ]


def test_dotted_column_resolves_for_table(
    nested_rows: list[dict[str, object]],
) -> None:
    """Table format must resolve dotted paths the same way raw/json/yaml do."""
    out = format_output(nested_rows, fmt="table", columns=["name", "summary_fields.project.name"])
    # Resolved value present.
    assert "playbooks" in out
    # Column header is the full dotted path (lock in: not bare "name" twice
    # or just the last segment).
    assert "summary_fields.project.name" in out
    # Both rows render — the missing-summary row resolves to None, not error.
    assert "alpha" in out and "beta" in out


def test_scalar_list_renders_comma_separated_for_human_formats() -> None:
    rows = [{"name": "alpha", "credentials": ["ssh", "vault"]}]
    raw = format_output(rows, fmt="raw", columns=["credentials"])
    table = format_output(rows, fmt="table", columns=["credentials"])
    # splitlines() matches the rest of this file — robust to trailing newlines.
    assert raw.splitlines() == ["ssh, vault"]
    assert "ssh, vault" in table


def test_nested_list_falls_back_to_repr() -> None:
    """Lists of dicts are not flattened — they're structured data the
    user probably wants to inspect via json/yaml, not collapse."""
    rows = [{"name": "alpha", "items": [{"id": 1}, {"id": 2}]}]
    raw = format_output(rows, fmt="raw", columns=["items"])
    assert "id" in raw
    assert "{" in raw  # repr-shaped, not "id, id"
