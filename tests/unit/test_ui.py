"""Unit tests for theme-aware UI rendering primitives."""

from __future__ import annotations

import io
import json
import re

import yaml

from untaped.output import format_output
from untaped.ui import ThemeSpec, UiContext


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def _has_ansi(value: str) -> bool:
    return "\x1b[" in value


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


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


def test_table_color_roles_emit_ansi_only_for_tty_stdout() -> None:
    theme = ThemeSpec(
        color_roles={
            "header": "bold cyan",
            "border": "green",
            "value": "yellow",
        }
    )

    tty_rendered = UiContext(stdout=TtyStringIO(), theme=theme).collection(
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )
    plain_rendered = UiContext(stdout=io.StringIO(), theme=theme).collection(
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )

    assert _has_ansi(tty_rendered)
    assert "\x1b[36m" in tty_rendered or "\x1b[1;36m" in tty_rendered
    assert "\x1b[32m" in tty_rendered
    assert "\x1b[33m" in tty_rendered
    assert "alpha" in tty_rendered
    assert not _has_ansi(plain_rendered)


def test_list_color_roles_style_keys_and_values_only_for_tty_stdout() -> None:
    theme = ThemeSpec(
        collection_view="list",
        detail_view="list",
        color_roles={"key": "cyan", "value": "magenta"},
    )

    collection = UiContext(stdout=TtyStringIO(), theme=theme).collection(
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )
    detail = UiContext(stdout=TtyStringIO(), theme=theme).detail(
        {"id": 1, "name": "alpha"},
        fmt="table",
    )
    plain = UiContext(stdout=io.StringIO(), theme=theme).collection(
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )

    assert _has_ansi(collection)
    assert _has_ansi(detail)
    assert "id:" in _strip_ansi(collection)
    assert "name: alpha" in _strip_ansi(detail)
    assert not _has_ansi(plain)


def test_structured_formats_ignore_color_roles_even_for_tty_stdout() -> None:
    rows = [{"id": 1, "name": "alpha"}]
    ui = UiContext(
        stdout=TtyStringIO(),
        theme=ThemeSpec(collection_view="list", color_roles={"key": "cyan", "value": "red"}),
    )

    assert not _has_ansi(ui.collection(rows, fmt="json"))
    assert not _has_ansi(ui.collection(rows, fmt="yaml"))
    assert not _has_ansi(ui.collection(rows, fmt="raw"))


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


def test_message_color_roles_style_full_line_only_for_tty_stderr() -> None:
    theme = ThemeSpec(
        symbols={"success": "+", "info": "i", "warning": "!", "error": "x"},
        color_roles={
            "success": "green",
            "info": "blue",
            "warning": "yellow",
            "error": "red",
        },
    )
    tty_stderr = TtyStringIO()
    plain_stderr = io.StringIO()

    tty_ui = UiContext(stderr=tty_stderr, theme=theme)
    plain_ui = UiContext(stderr=plain_stderr, theme=theme)
    for kind in ("success", "info", "warning", "error"):
        tty_ui.message(kind, f"{kind} text")
        plain_ui.message(kind, f"{kind} text")

    assert _has_ansi(tty_stderr.getvalue())
    assert "+ success text" in tty_stderr.getvalue()
    assert "warning: warning text" in tty_stderr.getvalue()
    assert not _has_ansi(plain_stderr.getvalue())


def test_success_message_preserves_plain_text_by_default() -> None:
    stderr = io.StringIO()
    ui = UiContext(stderr=stderr)

    ui.message("success", "created profile: dev")

    assert stderr.getvalue().splitlines() == ["created profile: dev"]
