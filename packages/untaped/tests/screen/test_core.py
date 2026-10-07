"""The screen contract: construction rules, commands and the theme tokens a frame serves."""

from __future__ import annotations

import re

import pytest
from rich import box

from screen.support import make_screen
from untaped.screen.core import (
    SHARED_KEYS,
    Binding,
    Cmd,
    Footer,
    Frame,
    Screen,
    is_inline,
    key_label,
)
from untaped.stability import Experimental, function_mark
from untaped.theme import BUILTIN_THEMES, ThemeSpec


def test_a_screen_with_its_required_declarations_is_built() -> None:
    screen = make_screen()
    assert (screen.layout, screen.keys) == ("full", ())


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_screen_refuses_an_empty_alternative(value: str) -> None:
    with pytest.raises(ValueError, match="alternative"):
        make_screen(alternative=value)


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_screen_refuses_an_empty_command(value: str) -> None:
    with pytest.raises(ValueError, match="command"):
        make_screen(command=value)


def test_screen_refuses_a_whitespace_only_alternative_and_command() -> None:
    with pytest.raises(ValueError):
        make_screen(command=" ", alternative=" ")


@pytest.mark.parametrize("key", list(SHARED_KEYS))
def test_screen_refuses_a_binding_to_each_shared_key(key: str) -> None:
    with pytest.raises(ValueError, match=re.escape(f"'{key}'")):
        make_screen(keys=(Binding(key, "mine", object()),))


def test_screen_accepts_bindings_that_are_not_shared() -> None:
    keys = (Binding("ctrl-r", "refresh", object()), Binding("x", "extra", None))
    assert make_screen(keys=keys).keys == keys


@pytest.mark.parametrize("key", ["space", "Ctrl-R", "ctrl-x", "ab", "", "\t", "Enter"])
def test_screen_refuses_a_binding_key_that_is_no_key_name(key: str) -> None:
    with pytest.raises(ValueError, match="not a key name"):
        make_screen(keys=(Binding(key, "mine", object()),))


def test_screen_accepts_the_space_bar_and_every_key_name_as_a_binding() -> None:
    keys = (
        Binding(" ", "toggle", object()),
        Binding("ctrl-u", "clear", None),
        Binding("é", "e", 1),
    )
    assert make_screen(keys=keys).keys == keys


def test_screen_refuses_an_unknown_layout() -> None:
    with pytest.raises(ValueError, match="layout"):
        make_screen(layout="floating")


def test_the_shared_keys_are_the_documented_ones() -> None:
    assert list(SHARED_KEYS) == ["esc", "ctrl-c", "tab", "shift-tab", "enter", "ctrl-s", "?"]


def test_cmd_refuses_write_with_suspend() -> None:
    with pytest.raises(ValueError, match="write"):
        Cmd(lambda: None, write=True, suspend=True)


def test_cmd_send_is_inline() -> None:
    message = object()
    cmd = Cmd.send(message)
    assert is_inline(cmd)
    assert cmd.fn() is message
    assert not is_inline(Cmd(lambda: message))


def test_cmd_send_compares_by_message_and_reads_well() -> None:
    assert Cmd.send(1) == Cmd.send(1)
    assert Cmd.send(1) != Cmd.send(2)
    assert "message=1" in repr(Cmd.send(1))


def test_the_shared_keys_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        SHARED_KEYS["x"] = SHARED_KEYS["esc"]  # type: ignore[index]
    with pytest.raises(TypeError):
        del SHARED_KEYS["esc"]  # type: ignore[attr-defined]


def test_cmd_name_defaults_to_the_function_name() -> None:
    def refresh() -> None:
        return None

    assert Cmd(refresh).name.endswith("refresh")
    assert Cmd(refresh, name="reload").name == "reload"


def test_cmd_name_of_a_callable_object_is_its_type() -> None:
    class Probe:
        def __call__(self) -> None:
            return None

    assert Cmd(Probe()).name == "Probe"


def test_screen_objects_are_marked_experimental() -> None:
    assert isinstance(function_mark(Screen), Experimental)
    assert isinstance(function_mark(Cmd), Experimental)


def test_frame_serves_the_themes_symbols_and_styles() -> None:
    frame = Frame(80, 24, BUILTIN_THEMES["plain"])
    assert frame.symbol("chosen") == ">"
    assert frame.ellipsis() == "..."
    assert frame.style("screen.accent") == "bold cyan"


def test_frame_falls_back_to_default_tokens() -> None:
    bare = ThemeSpec(symbols={"success": "ok"}, color_roles={"screen.error": "red"})
    frame = Frame(80, 24, bare)
    default = BUILTIN_THEMES["default"]
    assert frame.style("screen.accent") == default.color_roles["screen.accent"]
    assert frame.symbol("chosen") == default.symbols["chosen"]
    assert frame.symbol("success") == "ok"
    assert frame.style("screen.error") == "red"


def test_frame_keeps_a_symbol_the_theme_sets_to_empty() -> None:
    frame = Frame(80, 24, ThemeSpec(symbols={"ellipsis": ""}))
    assert frame.ellipsis() == ""


def test_frame_styles_a_screen_role_no_theme_defines_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(BUILTIN_THEMES, "default", ThemeSpec())
    assert Frame(80, 24, ThemeSpec()).style("screen.accent") == ""


def test_frame_refuses_an_undeclared_name() -> None:
    frame = Frame(80, 24, BUILTIN_THEMES["default"])
    with pytest.raises(ValueError, match="checkd") as symbol_error:
        frame.symbol("checkd")
    assert "ellipsis" in str(symbol_error.value)
    with pytest.raises(ValueError, match=r"screen\.acent") as style_error:
        frame.style("screen.acent")
    assert "screen.accent" in str(style_error.value)


@pytest.mark.parametrize("role", ["header", "error", "success", "border"])
def test_frame_style_serves_screen_roles_only_never_table_roles(role: str) -> None:
    with pytest.raises(ValueError, match="screen roles"):
        Frame(80, 24, BUILTIN_THEMES["default"]).style(role)


@pytest.mark.parametrize(
    ("border", "expected"),
    [("rounded", box.ROUNDED), ("square", box.SQUARE), ("ascii", box.ASCII), ("none", None)],
)
def test_frame_box_follows_the_border_style(border: str, expected: box.Box | None) -> None:
    theme = ThemeSpec(border=border)  # type: ignore[arg-type]
    assert Frame(80, 24, theme).box() is expected


def test_footer_lists_the_bindings_then_back_and_help() -> None:
    footer = Footer((Binding("ctrl-r", "refresh", None), Binding("x", "extra", None)))
    assert footer.entries() == (
        ("ctrl-r", "refresh"),
        ("x", "extra"),
        ("esc", "back"),
        ("?", "help"),
    )


def test_key_label_spells_out_the_space_bar() -> None:
    assert key_label(" ") == "space"
    assert [key_label(key) for key in ("x", "ctrl-r", "?")] == ["x", "ctrl-r", "?"]


def test_footer_shows_the_space_bar_as_a_word() -> None:
    footer = Footer((Binding(" ", "toggle", None),))
    assert footer.entries()[0] == ("space", "toggle")
    line = footer.line(Frame(80, 10, BUILTIN_THEMES["default"]))
    assert line.plain.startswith("space toggle · esc back")


def test_footer_line_is_exactly_the_frame_width() -> None:
    footer = Footer((Binding("ctrl-r", "refresh", None),))
    for width in (5, 20, 80):
        line = footer.line(Frame(width, 10, BUILTIN_THEMES["default"]))
        assert line.cell_len == width
    wide = footer.line(Frame(80, 10, BUILTIN_THEMES["default"]))
    assert wide.plain.rstrip() == "ctrl-r refresh · esc back · ? help"


def test_footer_keys_are_words_not_arrow_glyphs() -> None:
    footer = Footer((Binding("up", "move", None), Binding("left", "back a level", None)))
    assert all(key.isascii() for key, _label in footer.entries())


def test_footer_saving_replaces_the_hints() -> None:
    line = Footer(saving=True).line(Frame(30, 10, BUILTIN_THEMES["plain"]))
    assert line.plain.rstrip() == "saving..."
