"""Unit tests for theme-aware UI rendering primitives."""

from __future__ import annotations

import io
import json

import yaml

from untaped.output import format_output
from untaped.ui import ThemeSpec, UiContext


def test_collection_uses_theme_view_preferences_for_terminal_rendering() -> None:
    ui = UiContext(theme=ThemeSpec(collection_view="list"))

    rendered = ui.collection(
        [
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
        ],
        fmt="table",
    )

    assert "id: 1" in rendered
    assert "name: alpha" in rendered
    assert "id: 2" in rendered
    assert "name: beta" in rendered
    assert "┌" not in rendered
    assert "╭" not in rendered


def test_collection_theme_does_not_change_structured_formats() -> None:
    rows = [{"id": 1, "name": "alpha"}]
    ui = UiContext(theme=ThemeSpec(collection_view="list", border="square"))

    assert json.loads(ui.collection(rows, fmt="json")) == rows
    assert yaml.safe_load(ui.collection(rows, fmt="yaml")) == rows
    assert ui.collection(rows, fmt="raw").splitlines() == ["1"]


def test_collection_border_style_is_themeable_for_table_rendering() -> None:
    ui = UiContext(theme=ThemeSpec(border="square"))

    rendered = ui.collection([{"id": 1, "name": "alpha"}], fmt="table")

    assert "┌" in rendered
    assert "╭" not in rendered


def test_collection_border_none_renders_borderless_table() -> None:
    ui = UiContext(theme=ThemeSpec(border="none"))

    rendered = ui.collection([{"id": 1, "name": "alpha"}], fmt="table")

    assert "id" in rendered
    assert "alpha" in rendered
    assert "╭" not in rendered
    assert "┌" not in rendered
    assert "│" not in rendered
    assert "|" not in rendered


def test_format_output_accepts_explicit_theme_for_compatibility() -> None:
    rendered = format_output(
        [{"id": 1, "name": "alpha"}],
        fmt="table",
        theme=ThemeSpec(collection_view="list"),
    )

    assert "id: 1" in rendered
    assert "name: alpha" in rendered
    assert "╭" not in rendered


def test_detail_renders_single_object_without_wrapping_structured_formats() -> None:
    record = {"id": 1, "name": "alpha"}
    ui = UiContext()

    assert json.loads(ui.detail(record, fmt="json")) == record
    assert yaml.safe_load(ui.detail(record, fmt="yaml")) == record
    assert ui.detail(record, fmt="raw").splitlines() == ["1"]


def test_detail_uses_theme_view_preferences_for_terminal_rendering() -> None:
    ui = UiContext(theme=ThemeSpec(detail_view="table", border="square"))

    rendered = ui.detail({"id": 1, "name": "alpha"}, fmt="table")

    assert "field" in rendered
    assert "value" in rendered
    assert "name" in rendered
    assert "alpha" in rendered
    assert "┌" in rendered


def test_message_writes_semantic_status_to_stderr() -> None:
    stderr = io.StringIO()
    ui = UiContext(stderr=stderr, theme=ThemeSpec(symbols={"warning": "!"}))

    ui.message("warning", "check this")

    assert stderr.getvalue().splitlines() == ["! warning: check this"]


def test_success_message_preserves_plain_text_by_default() -> None:
    stderr = io.StringIO()
    ui = UiContext(stderr=stderr)

    ui.message("success", "created profile: dev")

    assert stderr.getvalue().splitlines() == ["created profile: dev"]
