"""Declared theme tokens: every built-in theme defines them, writes reject strays."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from rich.cells import cell_len
from rich.style import Style

from untaped.errors import ConfigError
from untaped.settings import validate_settings_section
from untaped.theme import (
    BUILTIN_THEMES,
    ROLE_NAMES,
    SCREEN_ROLE_NAMES,
    SCREEN_SYMBOLS,
    SYMBOL_NAMES,
    UiSettings,
    check_declared_tokens,
    resolve_theme,
)

THEMES = sorted(BUILTIN_THEMES)


@pytest.mark.parametrize("name", THEMES)
def test_every_builtin_theme_defines_every_declared_symbol_and_screen_role(name: str) -> None:
    theme = BUILTIN_THEMES[name]
    assert set(theme.symbols) == set(SYMBOL_NAMES)
    assert set(SCREEN_ROLE_NAMES) <= set(theme.color_roles)
    assert set(theme.color_roles) <= set(ROLE_NAMES)


@pytest.mark.parametrize("name", THEMES)
def test_every_builtin_theme_defines_the_caret_and_emphasis_roles_as_today_s_look(
    name: str,
) -> None:
    roles = BUILTIN_THEMES[name].color_roles
    assert {"screen.caret", "screen.emphasis", "screen.match"} <= set(SCREEN_ROLE_NAMES)
    assert Style.parse(roles["screen.caret"]) == Style(reverse=True)
    assert Style.parse(roles["screen.emphasis"]) == Style(bold=True)
    assert Style.parse(roles["screen.match"]) == Style(bold=True, underline=True)


def test_the_caret_and_emphasis_roles_are_declared_for_writes() -> None:
    ui = validate_settings_section(
        {"ui": {"color_roles": {"screen.caret": "underline", "screen.emphasis": "italic"}}}, "ui"
    )
    assert ui.color_roles == {"screen.caret": "underline", "screen.emphasis": "italic"}
    assert resolve_theme(ui).color_roles["screen.caret"] == "underline"


@pytest.mark.parametrize("name", THEMES)
def test_every_screen_symbol_is_one_cell_wide(name: str) -> None:
    symbols = BUILTIN_THEMES[name].symbols
    for token in SCREEN_SYMBOLS:
        if name == "plain" and token == "ellipsis":
            continue
        assert cell_len(symbols[token]) == 1, (name, token)
    if name == "plain":
        assert symbols["ellipsis"] == "..."


def test_plain_symbols_are_ascii() -> None:
    for token, glyph in BUILTIN_THEMES["plain"].symbols.items():
        assert glyph.isascii(), token


def test_screen_symbols_follow_the_design() -> None:
    assert BUILTIN_THEMES["default"].symbols["chosen"] == "▶"
    assert BUILTIN_THEMES["default"].symbols["unchecked"] == " "
    assert BUILTIN_THEMES["plain"].symbols["chosen"] == ">"
    assert BUILTIN_THEMES["plain"].symbols["checked"] == "x"
    assert BUILTIN_THEMES["plain"].symbols["on"] == "+"
    assert BUILTIN_THEMES["plain"].symbols["off"] == "x"


def test_cycle_and_tab_tokens_have_a_glyph_and_an_ascii_fallback() -> None:
    default = BUILTIN_THEMES["default"].symbols
    plain = BUILTIN_THEMES["plain"].symbols
    assert (default["cycle.left"], default["cycle.right"]) == ("\u2039", "\u203a")
    assert (plain["cycle.left"], plain["cycle.right"]) == ("<", ">")
    assert default["tab.active"] != default["tab.inactive"]
    assert plain["tab.active"] != plain["tab.inactive"]


def test_tree_tokens_have_a_glyph_and_an_ascii_fallback() -> None:
    default = BUILTIN_THEMES["default"].symbols
    plain = BUILTIN_THEMES["plain"].symbols
    assert (default["tree.open"], default["tree.closed"]) == ("\u25be", "\u25b8")
    assert (plain["tree.open"], plain["tree.closed"]) == ("v", ">")


def test_tag_tokens_have_a_glyph_and_an_ascii_fallback() -> None:
    default = BUILTIN_THEMES["default"].symbols
    plain = BUILTIN_THEMES["plain"].symbols
    assert (default["tag.add"], default["tag.remove"]) == ("+", "\u2715")
    assert (plain["tag.add"], plain["tag.remove"]) == ("+", "x")


@pytest.mark.parametrize("name", THEMES)
def test_screen_roles_are_valid_rich_styles(name: str) -> None:
    roles = BUILTIN_THEMES[name].color_roles
    for role in SCREEN_ROLE_NAMES:
        Style.parse(roles[role])


def test_unknown_symbol_name_is_refused_on_write() -> None:
    with pytest.raises(ValidationError) as caught:
        validate_settings_section({"ui": {"symbols": {"checkd": "x"}}}, "ui")
    message = str(caught.value)
    assert "checkd" in message
    assert all(name in message for name in ("checked", "chosen", "success"))


def test_unknown_color_role_name_is_refused_on_write() -> None:
    with pytest.raises(ValidationError) as caught:
        validate_settings_section({"ui": {"color_roles": {"screen.acent": "red"}}}, "ui")
    message = str(caught.value)
    assert "screen.acent" in message
    assert "screen.accent" in message


def test_declared_names_are_accepted_on_write() -> None:
    ui = validate_settings_section(
        {
            "ui": {
                "symbols": {"checked": "x", "success": "+"},
                "color_roles": {"screen.accent": "red"},
            }
        },
        "ui",
    )
    assert ui.symbols == {"checked": "x", "success": "+"}
    assert ui.color_roles == {"screen.accent": "red"}


def test_loading_an_unknown_name_stays_lenient() -> None:
    ui = UiSettings(symbols={"zzz": "x"}, color_roles={"zzz": "red"})
    theme = resolve_theme(ui)
    assert theme.symbols["zzz"] == "x"
    assert theme.color_roles["zzz"] == "red"


def test_check_declared_tokens_names_the_stray_and_lists_valid_ones() -> None:
    with pytest.raises(ConfigError, match=r"ui\.symbols\.zzz.*Valid symbols: "):
        check_declared_tokens(UiSettings(symbols={"zzz": "x"}))
    with pytest.raises(ConfigError, match=r"ui\.color_roles\.zzz.*Valid color roles: "):
        check_declared_tokens(UiSettings(color_roles={"zzz": "x"}))
    check_declared_tokens(UiSettings(symbols={"success": "y"}, color_roles={"key": "red"}))
