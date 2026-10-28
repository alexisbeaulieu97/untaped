"""Unit tests for theme-aware UI rendering primitives."""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stderr
from pathlib import Path

import pytest
import yaml

from core_test_support.runtime.run import quiet
from untaped.runtime.theme import BUILTIN_THEMES, UiSettings, resolve_theme
from untaped.sdk._run import Run
from untaped.sdk._theme import ThemeSpec
from untaped.sdk._ui import Terminal, build_terminal, collection, detail, print_styled
from untaped.sdk.errors import ConfigError


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def _has_ansi(value: str) -> bool:
    return "\x1b[" in value


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def test_collection_uses_theme_view_preferences_for_terminal_rendering() -> None:
    ui = Terminal(theme=ThemeSpec(collection_view="list"))

    rendered = collection(
        ui,
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
    ui = Terminal(theme=ThemeSpec(collection_view="list", border="square"))

    assert json.loads(collection(ui, rows, fmt="json")) == rows
    assert yaml.safe_load(collection(ui, rows, fmt="yaml")) == rows
    assert collection(ui, rows, fmt="raw").splitlines() == ["1"]


def test_collection_border_style_is_themeable_for_table_rendering() -> None:
    ui = Terminal(theme=ThemeSpec(border="square"))

    rendered = collection(ui, [{"id": 1, "name": "alpha"}], fmt="table")

    assert "┌" in rendered
    assert "╭" not in rendered


def test_collection_border_none_renders_borderless_table() -> None:
    ui = Terminal(theme=ThemeSpec(border="none"))

    rendered = collection(ui, [{"id": 1, "name": "alpha"}], fmt="table")

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

    tty_rendered = collection(
        Terminal(stdout=TtyStringIO(), theme=theme),
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )
    plain_rendered = collection(
        Terminal(stdout=io.StringIO(), theme=theme),
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

    listed = collection(
        Terminal(stdout=TtyStringIO(), theme=theme),
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )
    shown = detail(
        Terminal(stdout=TtyStringIO(), theme=theme),
        {"id": 1, "name": "alpha"},
        fmt="table",
    )
    plain = collection(
        Terminal(stdout=io.StringIO(), theme=theme),
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )

    assert _has_ansi(listed)
    assert _has_ansi(shown)
    assert "id:" in _strip_ansi(listed)
    assert "name: alpha" in _strip_ansi(shown)
    assert not _has_ansi(plain)


def test_structured_formats_ignore_color_roles_even_for_tty_stdout() -> None:
    rows = [{"id": 1, "name": "alpha"}]
    ui = Terminal(
        stdout=TtyStringIO(),
        theme=ThemeSpec(collection_view="list", color_roles={"key": "cyan", "value": "red"}),
    )

    assert not _has_ansi(collection(ui, rows, fmt="json"))
    assert not _has_ansi(collection(ui, rows, fmt="yaml"))
    assert not _has_ansi(collection(ui, rows, fmt="raw"))


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

    rendered = collection(
        Terminal(stdout=TtyStringIO() if tty else io.StringIO(), theme=theme),
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )

    assert "alpha" in rendered
    assert _has_ansi(rendered) is colored


def test_collection_accepts_explicit_theme() -> None:
    rendered = collection(
        Terminal(theme=ThemeSpec(collection_view="list")),
        [{"id": 1, "name": "alpha"}],
        fmt="table",
    )

    assert "id: 1" in rendered
    assert "name: alpha" in rendered
    assert "╭" not in rendered


def test_detail_renders_single_object_without_wrapping_structured_formats() -> None:
    record = {"id": 1, "name": "alpha"}
    ui = Terminal()

    assert json.loads(detail(ui, record, fmt="json")) == record
    assert yaml.safe_load(detail(ui, record, fmt="yaml")) == record
    assert detail(ui, record, fmt="raw").splitlines() == ["1"]


def test_detail_uses_theme_view_preferences_for_terminal_rendering() -> None:
    ui = Terminal(theme=ThemeSpec(detail_view="table", border="square"))

    rendered = detail(ui, {"id": 1, "name": "alpha"}, fmt="table")

    assert "field" in rendered
    assert "value" in rendered
    assert "name" in rendered
    assert "alpha" in rendered
    assert "┌" in rendered


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
    with redirect_stderr(stderr):
        out = collection(Terminal(stdout=stdout), rows, fmt=fmt, empty=empty)  # type: ignore[arg-type]
    if rendered is not None:
        assert out == rendered
    assert stdout.getvalue() == ""
    assert stderr.getvalue().strip() == hint


def test_quiet_mutes_the_empty_table_note() -> None:
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        collection(Terminal(stdout=io.StringIO(), quiet=True), [], fmt="table", empty=True)
    assert stderr.getvalue() == ""


def test_ui_context_reflects_active_verbose_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.runtime import verbose
    from untaped.runtime.run import entered, start_run
    from untaped.runtime.settings import get_settings

    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    get_settings.cache_clear()

    with entered(start_run()) as run:
        assert build_terminal(strict=False).verbose is False

        verbose.enable(run)
        try:
            assert build_terminal(strict=False).verbose is True
        finally:
            get_settings.cache_clear()


def test_ui_context_degrades_unknown_theme_to_default_when_not_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.runtime.settings import get_settings

    cfg = tmp_path / "config.yml"
    cfg.write_text("untaped:\n  ui:\n    theme: nonexistent\n")
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    get_settings.cache_clear()
    try:
        assert build_terminal(strict=False).theme == BUILTIN_THEMES["default"]
    finally:
        get_settings.cache_clear()


def test_ui_context_raises_on_unknown_theme_when_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.runtime.settings import get_settings

    cfg = tmp_path / "config.yml"
    cfg.write_text("format_version: 2\nuntaped:\n  ui:\n    theme: nonexistent\n")
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigError, match="unknown UI theme"):
            build_terminal(strict=True)
    finally:
        get_settings.cache_clear()


def test_resolve_theme_finds_quiet_preset_from_builtins_without_registry() -> None:
    theme = resolve_theme(UiSettings(theme="quiet"))

    assert theme.border == "none"
    assert theme.density == "compact"
    assert theme.collection_view == "list"
    assert theme.detail_view == "list"
    assert {k: v for k, v in theme.color_roles.items() if not k.startswith("screen.")} == {
        "key": "dim cyan",
        "success": "green",
        "info": "blue",
        "warning": "yellow",
        "error": "red",
    }


def test_ui_context_builds_default_prompt_backend_lazily_and_caches_it() -> None:
    """With no injected backend, the default terminal backend is built on
    first access (the only path that imports ``prompt_toolkit``) and reused."""
    from untaped.sdk.prompt.prompts import TerminalPromptBackend

    ui = Terminal()

    backend = ui.prompt_backend
    assert isinstance(backend, TerminalPromptBackend)
    assert ui.prompt_backend is backend  # cached, not rebuilt per access


# ---- --quiet gating --------------------------------------------------------


@pytest.mark.usefixtures("_isolated_config")
def test_ui_context_factory_reads_quiet_flag(home_run: Run) -> None:
    quiet(home_run)
    assert build_terminal().quiet is True


def test_default_prompt_backend_follows_reopened_terminal_streams() -> None:
    import io

    first = io.StringIO()
    ui = Terminal(stdin=first)
    old = ui.prompt_backend
    first.close()
    second = io.StringIO()
    ui.stdin = second
    current = ui.prompt_backend
    assert current is not old
    assert current.stdin is second
    assert ui.prompt_backend is current


def test_injected_prompt_backend_survives_stream_changes() -> None:
    from untaped.sdk.testing import ScriptedPromptBackend

    injected = ScriptedPromptBackend(confirms=[])
    ui = Terminal(prompt_backend=injected, stdin=io.StringIO())
    ui.stdin = io.StringIO()
    ui.stderr = io.StringIO()
    assert ui.prompt_backend is injected


def test_styled_writes_rich_text_to_stdout_with_color_only_on_a_tty() -> None:
    from rich.text import Text

    piped, tty = io.StringIO(), TtyStringIO()
    line = Text("ok: host", style="green")

    print_styled(Terminal(stdout=piped), line)
    print_styled(Terminal(stdout=tty), line)

    assert piped.getvalue() == "ok: host\n"
    assert _has_ansi(tty.getvalue())
    assert _strip_ansi(tty.getvalue()) == "ok: host\n"


def test_styled_tail_follows_verbatim_unwrapped_and_unexpanded() -> None:
    from rich.text import Text

    tty = TtyStringIO()
    tail = "\tweb01 : ok=1 " + "x" * 200

    print_styled(Terminal(stdout=tty), Text("[deploy] ", style="dim cyan"), tail=tail)
    print_styled(Terminal(stdout=tty), Text(""), tail=tail)

    first, second = tty.getvalue().splitlines()
    assert _has_ansi(first)
    assert _strip_ansi(first) == f"[deploy] {tail}"
    assert second == tail


def test_styled_truncate_ends_each_too_wide_line_in_an_ellipsis(monkeypatch) -> None:
    from rich.text import Text

    monkeypatch.setenv("COLUMNS", "20")
    wrapped, truncated = io.StringIO(), io.StringIO()
    text = Text("├── acme/a-long-name@main  notes\n└── short")

    print_styled(Terminal(stdout=wrapped), text)
    print_styled(Terminal(stdout=truncated), text, truncate=True)

    assert len(wrapped.getvalue().splitlines()) > 2
    assert truncated.getvalue() == "├── acme/a-long-nam…\n└── short\n"


def _closed() -> io.StringIO:
    stream = io.StringIO()
    stream.close()
    return stream


@pytest.mark.parametrize(
    ("stdin", "expected"),
    [(TtyStringIO(), True), (io.StringIO(), False), (_closed(), False)],
    ids=["terminal", "pipe", "closed"],
)
def test_can_prompt_follows_the_context_stdin(stdin: io.StringIO, expected: bool) -> None:
    assert Terminal(stdin=stdin).can_prompt is expected


def test_can_prompt_is_true_inside_terminal_even_with_piped_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("untaped.sdk._ui.open_controlling_terminal", TtyStringIO)
    ui = Terminal(stdin=io.StringIO("piped data"))

    assert ui.can_prompt is False
    with ui.terminal():
        assert ui.can_prompt is True
    assert ui.can_prompt is False
