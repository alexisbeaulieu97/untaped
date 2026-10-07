"""The screen contract: construction rules, commands and the theme tokens a frame serves."""

from __future__ import annotations

import re

import pytest
from rich import box

from untaped.screen.core import SHARED_KEYS, Binding, Cmd, Frame, Screen, is_inline
from untaped.stability import Experimental, function_mark
from untaped.theme import BUILTIN_THEMES, ThemeSpec


def _screen(**overrides: object) -> Screen[int, int]:
    fields: dict[str, object] = {
        "init": lambda: (0, []),
        "update": lambda model, message: (model, []),
        "view": lambda model, frame: str(model),
        "title": "Counter",
        "command": "untaped count",
        "alternative": "untaped count --format json",
    }
    fields.update(overrides)
    return Screen(**fields)  # type: ignore[arg-type]


def test_a_screen_with_its_required_declarations_is_built() -> None:
    screen = _screen()
    assert (screen.layout, screen.keys) == ("full", ())


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_screen_refuses_an_empty_alternative(value: str) -> None:
    with pytest.raises(ValueError, match="alternative"):
        _screen(alternative=value)


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_screen_refuses_an_empty_command(value: str) -> None:
    with pytest.raises(ValueError, match="command"):
        _screen(command=value)


def test_screen_refuses_a_whitespace_only_alternative_and_command() -> None:
    with pytest.raises(ValueError):
        _screen(command=" ", alternative=" ")


@pytest.mark.parametrize("key", list(SHARED_KEYS))
def test_screen_refuses_a_binding_to_each_shared_key(key: str) -> None:
    with pytest.raises(ValueError, match=re.escape(f"'{key}'")):
        _screen(keys=(Binding(key, "mine", object()),))


def test_screen_accepts_bindings_that_are_not_shared() -> None:
    keys = (Binding("ctrl-r", "refresh", object()), Binding("x", "extra", None))
    assert _screen(keys=keys).keys == keys


def test_screen_refuses_an_unknown_layout() -> None:
    with pytest.raises(ValueError, match="layout"):
        _screen(layout="floating")


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
    bare = ThemeSpec(symbols={"success": "ok"}, color_roles={"error": "red"})
    frame = Frame(80, 24, bare)
    default = BUILTIN_THEMES["default"]
    assert frame.style("screen.accent") == default.color_roles["screen.accent"]
    assert frame.symbol("chosen") == default.symbols["chosen"]
    assert frame.symbol("success") == "ok"
    assert frame.style("error") == "red"


def test_frame_keeps_a_symbol_the_theme_sets_to_empty() -> None:
    frame = Frame(80, 24, ThemeSpec(symbols={"ellipsis": ""}))
    assert frame.ellipsis() == ""


def test_frame_styles_a_role_no_theme_defines_as_empty() -> None:
    assert Frame(80, 24, BUILTIN_THEMES["default"]).style("header") == ""


def test_frame_refuses_an_undeclared_name() -> None:
    frame = Frame(80, 24, BUILTIN_THEMES["default"])
    with pytest.raises(KeyError, match="checkd"):
        frame.symbol("checkd")
    with pytest.raises(KeyError, match=r"screen\.acent"):
        frame.style("screen.acent")


@pytest.mark.parametrize(
    ("border", "expected"),
    [("rounded", box.ROUNDED), ("square", box.SQUARE), ("ascii", box.ASCII), ("none", None)],
)
def test_frame_box_follows_the_border_style(border: str, expected: box.Box | None) -> None:
    theme = ThemeSpec(border=border)  # type: ignore[arg-type]
    assert Frame(80, 24, theme).box() is expected
