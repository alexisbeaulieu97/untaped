"""How a runtime frame is drawn: literal text, no highlighter, a pinned footer."""

from __future__ import annotations

from typing import Any

import pytest
from rich.text import Text

from screen.support import Probe, make_screen
from untaped.screen.core import Binding, Frame, Key, Screen
from untaped.screen.runtime import Runtime, SyncHost, capture_console
from untaped.testing.screens import rendered_text
from untaped.theme import BUILTIN_THEMES, ThemeSpec


def _screen(view_text: str, *, layout: str = "full") -> Screen[Any, Any]:
    return make_screen(view=lambda model, frame: view_text, layout=layout)


def _runtime(screen: Screen[Any, Any], size: tuple[int, int] = (40, 8)) -> Runtime[Any, Any]:
    runtime = Runtime(screen, SyncHost(), theme=BUILTIN_THEMES["default"], size=size)
    runtime.start()
    return runtime


@pytest.mark.parametrize("text", ["[WIP] fix [/]", ":smile:", "[bold]x"])
def test_a_view_string_is_literal_text(text: str) -> None:
    runtime = _runtime(_screen(text))
    assert rendered_text(runtime.renderable(), 40, 8).splitlines()[0] == text


def test_the_highlighter_is_off() -> None:
    runtime = _runtime(_screen("count 42 and True"))
    console = capture_console(40, 8, color_system="truecolor", no_color=False)
    console.print(runtime.renderable(), end="")
    first_line = console.file.getvalue().split("\n")[0]  # type: ignore[attr-defined]
    assert "42" in first_line
    assert "\x1b" not in first_line


def test_a_text_view_keeps_its_own_style() -> None:
    screen = make_screen(view=lambda model, frame: Text("styled", style="bold"))
    console = capture_console(40, 8, color_system="truecolor", no_color=False)
    console.print(_runtime(screen).renderable(), end="")
    assert "\x1b[1mstyled" in console.file.getvalue()  # type: ignore[attr-defined]


def test_footer_is_pinned_to_the_last_row() -> None:
    lines = rendered_text(_runtime(_screen("top")).renderable(), 40, 8).splitlines()
    assert len(lines) == 8
    assert lines[0] == "top"
    assert lines[-1] == "esc back · ? help"
    assert all(line == "" for line in lines[1:-1])


def test_the_view_is_given_the_rows_above_the_footer() -> None:
    seen: list[Frame] = []

    def view(model: int, frame: Frame) -> str:
        seen.append(frame)
        return "x"

    _runtime(make_screen(view=view), (50, 10)).renderable()
    assert (seen[-1].width, seen[-1].height) == (50, 9)
    assert seen[-1].theme is BUILTIN_THEMES["default"]


def test_an_inline_screen_has_no_fixed_height() -> None:
    lines = rendered_text(_runtime(_screen("one line", layout="inline")).renderable(), 40, 8)
    assert lines.splitlines() == ["one line", "esc back · ? help"]


def test_a_long_footer_is_cut_with_the_ellipsis() -> None:
    probe = Probe()
    runtime = Runtime(probe.screen, SyncHost(), theme=BUILTIN_THEMES["default"], size=(10, 4))
    runtime.start()
    last = rendered_text(runtime.renderable(), 10, 4).splitlines()[-1]
    assert last == "esc back …"


def test_the_help_overlay_follows_the_themes_border() -> None:
    plain = Runtime(_screen("x"), SyncHost(), theme=BUILTIN_THEMES["plain"], size=(40, 14))
    plain.start()
    plain.send(Key("?"))
    text = rendered_text(plain.renderable(), 40, 14)
    assert "+-" in text
    assert text.isascii()


def test_the_help_overlay_without_a_border_draws_no_box() -> None:
    theme = ThemeSpec(border="none")
    runtime = Runtime(_screen("x"), SyncHost(), theme=theme, size=(40, 14))
    runtime.start()
    runtime.send(Key("?"))
    text = rendered_text(runtime.renderable(), 40, 14)
    assert not any(char in text for char in "+|─│╭╮╰╯┌┐└┘")
    assert "esc" in text
    assert "esc closes help" in text


def test_the_help_overlay_names_the_space_bar() -> None:
    screen = make_screen(keys=(Binding(" ", "toggle", object()),))
    runtime = Runtime(screen, SyncHost(), theme=BUILTIN_THEMES["default"], size=(40, 14))
    runtime.start()
    runtime.send(Key("?"))
    lines = rendered_text(runtime.renderable(), 40, 14).splitlines()
    assert any("space" in line and "toggle" in line for line in lines)


def test_capture_console_needs_its_colour_choices_spelled_out() -> None:
    with pytest.raises(TypeError):
        capture_console(10, 2)  # type: ignore[call-arg]
