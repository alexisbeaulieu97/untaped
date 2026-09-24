"""Unit tests for theme-aware UI rendering primitives."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
import yaml

from untaped.errors import ConfigError
from untaped.theme import BUILTIN_THEMES, ThemeSpec, UiSettings, resolve_theme
from untaped.ui import UiContext, ui_context


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


@pytest.mark.parametrize(
    ("env", "tty", "colored"),
    [
        ({"NO_COLOR": "1"}, True, False),
        ({"FORCE_COLOR": "1"}, False, True),
        ({"NO_COLOR": "1", "FORCE_COLOR": "1"}, True, False),
        # ``FORCE_COLOR=`` (empty) is not a request to force color (matches NO_COLOR).
        ({"FORCE_COLOR": ""}, False, False),
    ],
    ids=["no-color-on-tty", "force-color-off-tty", "no-color-wins", "empty-force-color"],
)
def test_color_env_overrides_tty_detection(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], tty: bool, colored: bool
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    theme = ThemeSpec(color_roles={"header": "bold cyan", "value": "yellow"})

    rendered = UiContext(stdout=TtyStringIO() if tty else io.StringIO(), theme=theme).collection(
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )

    assert "alpha" in rendered
    assert _has_ansi(rendered) is colored


def test_collection_accepts_explicit_theme() -> None:
    rendered = UiContext(theme=ThemeSpec(collection_view="list")).collection(
        [{"id": 1, "name": "alpha"}],
        fmt="table",
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


def test_progress_reports_on_stderr_and_keeps_stdout_clean() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    ui = UiContext(stdout=stdout, stderr=stderr)

    with ui.progress("Resolving deps") as p:
        p.update("installing", new_phase=True)

    assert stdout.getvalue() == ""
    assert "Resolving deps" in stderr.getvalue()
    assert "installing" in stderr.getvalue()


def test_progress_verbose_context_passes_through_without_animation() -> None:
    stderr = io.StringIO()
    ui = UiContext(stdout=io.StringIO(), stderr=stderr, verbose=True)

    with ui.progress("Resolving deps") as p:
        p.update("downloading")

    output = stderr.getvalue()
    assert "Resolving deps" in output
    assert "downloading" in output
    assert "\r" not in output


@pytest.mark.parametrize(
    ("rows", "fmt", "empty", "rendered", "hint"),
    [
        ([], "table", "No plugins installed.", "", "No plugins installed."),
        ([], "table", None, "", ""),
        ([], "json", "No results.", "[]", ""),
        ([], "yaml", "No results.", "[]", ""),
        ([], "raw", "No results.", "", ""),
        ([{"name": "alpha"}], "table", "No results.", None, ""),
    ],
    ids=["table-hint", "table-no-hint", "json", "yaml", "raw", "non-empty"],
)
def test_empty_hint_goes_to_stderr_only_for_an_empty_table(
    rows: list[dict[str, str]], fmt: str, empty: str | None, rendered: str | None, hint: str
) -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    out = UiContext(stdout=stdout, stderr=stderr).collection(rows, fmt=fmt, empty=empty)  # type: ignore[arg-type]
    if rendered is not None:
        assert out == rendered
    assert stdout.getvalue() == ""
    assert stderr.getvalue().strip() == hint


def test_ui_context_reflects_active_verbose_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped import verbose
    from untaped.settings import get_settings

    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    get_settings.cache_clear()
    verbose.reset()

    assert ui_context(strict=False).verbose is False

    verbose.enable()
    try:
        assert ui_context(strict=False).verbose is True
    finally:
        verbose.reset()
        get_settings.cache_clear()


def test_ui_context_degrades_unknown_theme_to_default_when_not_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.settings import get_settings

    cfg = tmp_path / "config.yml"
    cfg.write_text("ui:\n  theme: nonexistent\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    try:
        assert ui_context(strict=False).theme == BUILTIN_THEMES["default"]
    finally:
        get_settings.cache_clear()


def test_ui_context_raises_on_unknown_theme_when_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.settings import get_settings

    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    ui:\n      theme: nonexistent\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigError, match="unknown UI theme"):
            ui_context(strict=True)
    finally:
        get_settings.cache_clear()


def test_resolve_theme_finds_quiet_preset_from_builtins_without_registry() -> None:
    theme = resolve_theme(UiSettings(theme="quiet"))

    assert theme.border == "none"
    assert theme.density == "compact"
    assert theme.collection_view == "list"
    assert theme.detail_view == "list"
    assert theme.color_roles == {
        "key": "dim cyan",
        "success": "green",
        "info": "blue",
        "warning": "yellow",
        "error": "red",
    }


def test_ui_context_builds_default_prompt_backend_lazily_and_caches_it() -> None:
    """With no injected backend, the default prompt_toolkit backend is built on
    first access (the only path that imports ``prompt_toolkit``) and reused."""
    from untaped.prompts import PromptToolkitPromptBackend

    ui = UiContext()

    backend = ui.prompt_backend
    assert isinstance(backend, PromptToolkitPromptBackend)
    assert ui.prompt_backend is backend  # cached, not rebuilt per access


# ---- --quiet gating --------------------------------------------------------


@pytest.mark.parametrize(
    ("quiet", "level", "shown"),
    [
        (True, "success", False),
        (True, "info", False),
        (True, "warning", True),
        (True, "error", True),
        (False, "success", True),
    ],
)
def test_quiet_suppresses_only_success_and_info(quiet: bool, level: str, shown: bool) -> None:
    buf = io.StringIO()
    UiContext(quiet=quiet, stderr=buf).message(level, "the message")  # type: ignore[arg-type]
    assert ("the message" in buf.getvalue()) is shown


def test_ui_context_factory_reads_quiet_flag(_isolated_config: Path) -> None:
    from untaped.quiet import enable, reset

    try:
        enable()
        assert ui_context().quiet is True
    finally:
        reset()


def test_default_prompt_backend_follows_reopened_terminal_streams() -> None:
    import io

    first = io.StringIO()
    ui = UiContext(stdin=first)
    old = ui.prompt_backend
    first.close()
    second = io.StringIO()
    ui.stdin = second
    current = ui.prompt_backend
    assert current is not old
    assert current.stdin is second
    assert ui.prompt_backend is current


def test_injected_prompt_backend_survives_stream_changes() -> None:
    from untaped.testing import ScriptedPromptBackend

    injected = ScriptedPromptBackend(confirms=[])
    ui = UiContext(prompt_backend=injected, stdin=io.StringIO())
    ui.stdin = io.StringIO()
    ui.stderr = io.StringIO()
    assert ui.prompt_backend is injected


def test_styled_writes_rich_text_to_stdout_with_color_only_on_a_tty() -> None:
    from rich.text import Text

    piped, tty = io.StringIO(), TtyStringIO()
    line = Text("ok: host", style="green")

    UiContext(stdout=piped).styled(line)
    UiContext(stdout=tty).styled(line)

    assert piped.getvalue() == "ok: host\n"
    assert _has_ansi(tty.getvalue())
    assert _strip_ansi(tty.getvalue()) == "ok: host\n"


def test_styled_err_writes_to_stderr_and_ignores_quiet() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()

    UiContext(stdout=stdout, stderr=stderr, quiet=True).styled("PLAY [all]", err=True)

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "PLAY [all]\n"
