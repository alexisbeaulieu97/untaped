"""The text components through ``drive_screen``: editing, completion, errors, secrets, themes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from screen.gallery import field_of, lines, render_styled, role, run_solo, style_of
from untaped.screen.components.choices import Check, ListItem, Select
from untaped.screen.components.inputs import NumberInput, PathInput, SecretInput, TextInput
from untaped.screen.core import Cancel, Frame, Key, NextField, Paste, Resize
from untaped.stability import Experimental, function_mark
from untaped.theme import BUILTIN_THEMES

THEMES = sorted(BUILTIN_THEMES)


def _typed(component: Any, *keys: str) -> Any:
    """``component`` after ``keys`` were typed into it (no screen, so nothing else reacts)."""
    for key in keys:
        component = component.update(Key(key))[0]
    return component


def _view(component: object, theme: str = "default", *, width: int = 40, focused: bool = True):
    return render_styled(
        component.view(Frame(width, 20, BUILTIN_THEMES[theme]), focused=focused, width=width),  # type: ignore[attr-defined]
        width=width,
    )


# --- editing --------------------------------------------------------------------


def test_typing_inserts_at_the_caret_and_the_caret_moves_with_the_arrows() -> None:
    run = run_solo(TextInput("Name"), "a", "b", "c", "left", "left", "X", "end", "!")

    assert field_of(run).value == "aXbc!"
    assert field_of(run).cursor == 5


def test_home_end_backspace_and_delete() -> None:
    run = run_solo(TextInput("Name", "hello"), "home", "delete", "end", "backspace")
    assert (field_of(run).value, field_of(run).cursor) == ("ell", 3)

    run = run_solo(TextInput("Name", "hello"), "left", "left", "backspace", "delete")
    assert (field_of(run).value, field_of(run).cursor) == ("heo", 2)


def test_ctrl_u_clears_and_ctrl_w_deletes_the_previous_word() -> None:
    assert field_of(run_solo(TextInput("Name", "one two"), "ctrl-u")).value == ""

    run = run_solo(TextInput("Name", "one two three"), "ctrl-w")
    assert field_of(run).value == "one two "
    run = run_solo(TextInput("Name", "one two three"), "left", "left", "ctrl-w")
    assert field_of(run).value == "one two ee"
    assert field_of(run).cursor == len("one two ")


def test_the_space_bar_is_a_character() -> None:
    assert field_of(run_solo(TextInput("Name"), "a", " ", "b")).value == "a b"


def test_question_mark_in_a_text_input_is_text() -> None:
    run = run_solo(TextInput("URL"), "?", "a", "?")

    assert field_of(run).value == "?a?"
    assert "Keys" not in run.frame  # the help overlay did not open
    assert run.outcome is None


def test_pasted_text_lands_in_the_value_without_line_breaks() -> None:
    run = run_solo(TextInput("Name", "ab", cursor=1), Paste("X\nY\tZ"))

    assert field_of(run).value == "aXYZb"
    assert field_of(run).cursor == 4


@pytest.mark.parametrize(
    "component",
    [TextInput("a", "xy"), SecretInput("a", SecretStr("xy")), NumberInput("a", "12")],
    ids=["text", "secret", "number"],
)
@pytest.mark.parametrize(
    "message",
    [Key("enter"), Key("esc"), Key("tab"), Key("shift-tab"), Key("ctrl-s"), Key("up"), Key("down"),
     Key("ctrl-r"), NextField(), Resize(10, 10), "not a message"],
)  # fmt: skip
def test_a_message_a_text_component_does_not_consume_returns_the_same_object(
    component: object, message: object
) -> None:
    result, cmds = component.update(message)  # type: ignore[attr-defined]

    assert result is component
    assert cmds == []


@pytest.mark.parametrize(
    "component",
    [TextInput("a"), SecretInput("a"), NumberInput("a")],
    ids=["text", "secret", "number"],
)
@pytest.mark.parametrize("key", ["left", "backspace", "home", "ctrl-u", "ctrl-w"])
def test_an_editing_key_that_changes_nothing_returns_the_same_object(
    component: object, key: str
) -> None:
    assert component.update(Key(key))[0] is component  # type: ignore[attr-defined]


def test_an_edit_clears_a_stale_error_but_a_caret_move_keeps_it() -> None:
    component = TextInput("Name", "ab").with_error("Taken.")

    assert component.update(Key("left"))[0].error == "Taken."
    assert component.update(Key("c"))[0].error == ""


def test_a_long_value_scrolls_to_keep_the_caret_in_view_and_unfocused_shows_the_start() -> None:
    value = "abcdefghijklmnopqrstuvwxyz" * 2
    run = run_solo(TextInput("Name", value), width=24)
    assert "wxyz" in lines(run.frame)[1]  # the caret is past the end: the tail is shown
    assert "abcde" not in lines(run.frame)[1]

    quiet = run_solo(TextInput("Name", value), width=24, focused=False)
    assert lines(quiet.frame)[1].startswith("│ abcdefghij")
    assert "…" in lines(quiet.frame)[1]  # cut with the theme's ellipsis


def test_the_caret_is_a_reversed_character_and_a_reversed_space_at_the_end() -> None:
    segments = _view(TextInput("Name", "abc", cursor=1))
    assert style_of(segments, "b").reverse
    assert not style_of(segments, "a").reverse

    at_end = _view(TextInput("Name", "abc"))
    assert any(s.text == " " and s.style is not None and s.style.reverse for s in at_end)
    assert not any(
        s.style is not None and s.style.reverse for s in _view(TextInput("N", "abc"), focused=False)
    )


def test_an_empty_input_shows_its_placeholder_muted() -> None:
    theme = BUILTIN_THEMES["default"]
    segments = _view(TextInput("Name", placeholder="your name"), focused=False)

    assert style_of(segments, "your name").color == role(theme, "screen.muted").color


# --- errors and validation --------------------------------------------------------


def test_error_draws_a_red_border_and_message() -> None:
    theme = BUILTIN_THEMES["default"]
    segments = _view(TextInput("Timeout", "0", help="Seconds.").with_error("Must be 1 or more."))
    red = role(theme, "screen.error").color

    assert style_of(segments, "╭").color == red
    assert style_of(segments, "Must be 1 or more.").color == red
    assert not any("Seconds." in s.text for s in segments)  # the error replaces the help
    frame = run_solo(TextInput("Timeout", "0").with_error("Must be 1 or more.")).frame
    assert "Must be 1 or more." in frame


def test_help_is_muted_below_the_box_and_the_focused_border_is_the_ring() -> None:
    theme = BUILTIN_THEMES["default"]
    segments = _view(TextInput("URL", help="The controller address."))

    assert style_of(segments, "The controller address.").color == role(theme, "screen.muted").color
    assert style_of(segments, "╭").color == role(theme, "screen.focus").color
    unfocused = _view(TextInput("URL"), focused=False)
    assert style_of(unfocused, "╭").color == role(theme, "screen.border").color


def test_validation_runs_on_validate_and_never_while_typing() -> None:
    calls: list[str] = []

    def validator(value: str) -> str:
        calls.append(value)
        return "Must start with https." if not value.startswith("https") else ""

    component = TextInput("URL", "http://x", validator=validator)
    run = run_solo(component, "y")

    assert calls == []
    assert field_of(run).validate() == "Must start with https."
    assert field_of(run).error == ""  # validate reports, it does not set
    assert field_of(run).with_error(field_of(run).validate()).error == "Must start with https."
    assert TextInput("URL").validate() == ""


# --- completion -----------------------------------------------------------------


def _fruit(prefix: str) -> list[str]:
    return [name for name in ("apple", "apricot", "banana") if name.startswith(prefix)]


def test_a_completion_list_opens_under_the_value_while_focused() -> None:
    run = run_solo(TextInput("Fruit", complete=_fruit), "a")
    assert "apple" in run.frame
    assert "apricot" in run.frame
    assert "banana" not in run.frame

    unfocused = run_solo(TextInput("Fruit", complete=_fruit), "a", focused=False)
    assert "apple" not in unfocused.frame


def test_a_candidate_equal_to_the_value_is_not_offered() -> None:
    assert _typed(TextInput("Fruit", "banan", complete=_fruit), "a").completing is False


def test_up_and_down_choose_a_candidate_and_tab_accepts_it() -> None:
    run = run_solo(TextInput("Fruit", complete=_fruit), "a", "down", "tab")

    assert field_of(run).value == "apricot"
    assert field_of(run).cursor == len("apricot")


def test_tab_accepts_a_completion_before_moving_focus() -> None:
    run = run_solo(TextInput("Fruit", "a", complete=_fruit), "p", "tab")

    assert field_of(run).value == "apple"
    assert run.model.unhandled == ()  # no NextField reached the screen

    run = run_solo(TextInput("Fruit", "a", complete=_fruit), "p", "tab", "tab")
    assert run.model.unhandled == (NextField(),)  # nothing left to accept: tab moves on


def test_tab_with_no_completions_is_left_to_the_screen() -> None:
    run = run_solo(TextInput("Name", "x"), "tab")

    assert run.model.unhandled == (NextField(),)


def test_esc_closes_an_open_completion_list_first() -> None:
    run = run_solo(TextInput("Fruit", complete=_fruit), "a", "esc")
    assert run.outcome is None
    assert "apple" not in run.frame
    assert field_of(run).completing is False

    run = run_solo(TextInput("Fruit", complete=_fruit), "a", "esc", "esc")
    assert run.outcome == Cancel()  # closed: Back still works

    run = run_solo(TextInput("Name", "a"), "esc")
    assert run.outcome == Cancel()


def test_typing_reopens_a_closed_completion_list() -> None:
    run = run_solo(TextInput("Fruit", complete=_fruit), "a", "esc", "p")

    assert field_of(run).completing
    assert "apple" in run.frame


def test_up_and_down_without_a_list_and_at_the_ends_return_the_same_object() -> None:
    plain = TextInput("Name", "a")
    assert plain.update(Key("down"))[0] is plain
    open_list = _typed(TextInput("Fruit", complete=_fruit), "a")
    assert open_list.update(Key("up"))[0] is open_list  # already on the first candidate
    last = open_list.update(Key("down"))[0]
    assert last.update(Key("down"))[0] is last


def test_a_long_candidate_list_shows_a_window_around_the_choice() -> None:
    many = TextInput("Item", complete=lambda text: [f"item{n:02d}" for n in range(20)])
    run = run_solo(many, "i", *["down"] * 12, size=(60, 30))

    assert "item12" in run.frame
    assert "item00" not in run.frame
    assert sum("item" in line for line in run.frame.splitlines()) == 5


# --- paths ------------------------------------------------------------------------


def test_path_input_completes_directories(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpine").mkdir()
    (tmp_path / "alpha.txt").write_text("x")
    (tmp_path / ".hidden").mkdir()
    typed = f"{tmp_path}/al"

    run = run_solo(PathInput("Path", typed[:-1]), "l")
    assert field_of(run).matches == (
        f"{tmp_path}/alpha/",
        f"{tmp_path}/alpine/",
        f"{tmp_path}/alpha.txt",
    )  # directories first, each ending in a separator

    run = run_solo(PathInput("Path", typed[:-1]), "l", "tab")
    assert field_of(run).value == f"{tmp_path}/alpha/"

    after_slash = _typed(PathInput("Path", str(tmp_path)), "/")
    assert after_slash.matches
    assert not any(".hidden" in match for match in after_slash.matches)
    assert _typed(PathInput("Path", f"{tmp_path}/."), "h").matches == (f"{tmp_path}/.hidden/",)


def test_path_input_keeps_the_tilde_the_user_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "Documents").mkdir()

    run = run_solo(PathInput("Path", "~/Do"), "c", "tab")

    assert field_of(run).value == "~/Documents/"  # never rewritten to the home directory


def test_path_input_offers_nothing_for_an_empty_or_unreadable_path(tmp_path: Path) -> None:
    assert PathInput("Path").matches == ()
    assert _typed(PathInput("Path", f"{tmp_path}/missing/"), "x").matches == ()
    assert _typed(PathInput("Path"), "a", "backspace").completing is False


# --- secrets ----------------------------------------------------------------------


def test_secret_never_appears_in_a_frame() -> None:
    secret = "s3cr3t-t0ken-value"
    keys = [*secret, Paste("PASTED-secret"), "left", "backspace"]
    run = run_solo(SecretInput("Token"), *keys)

    typed = field_of(run).value.get_secret_value()
    assert typed == "s3cr3t-t0ken-valuePASTED-secrt"  # the component holds it...
    for frame in run.frames:  # ...and no frame shows any of it
        assert secret not in frame
        assert "PASTED" not in frame
        assert "s3cr3t" not in frame
    for text in (repr(field_of(run)), str(field_of(run)), repr(run.model), repr(run)):
        assert "s3cr3t" not in text
        assert "PASTED" not in text
    assert "t0ken" not in repr(SecretInput("Token", SecretStr("t0ken")))


def test_a_secret_error_message_cannot_carry_the_secret_unless_the_validator_puts_it_there() -> (
    None
):
    component = SecretInput(
        "Token", SecretStr("abc"), validator=lambda v: "Too short." if len(v) < 8 else ""
    )

    assert component.validate() == "Too short."
    assert "abc" not in repr(component.with_error(component.validate()))


def test_secret_masks_with_the_theme_symbol_and_plain_uses_ascii() -> None:
    default = run_solo(SecretInput("Token"), *"abcd")
    assert "••••" in default.frame

    plain = run_solo(SecretInput("Token"), *"abcd", theme=BUILTIN_THEMES["plain"])
    assert "****" in plain.frame
    assert plain.frame.isascii()


def test_secret_caret_sits_among_the_masks_and_edits_the_hidden_text() -> None:
    run = run_solo(SecretInput("Token"), "a", "b", "c", "left", "left", "X")

    assert field_of(run).value.get_secret_value() == "aXbc"
    assert field_of(run).value == SecretStr("aXbc")
    segments = _view(SecretInput("Token", SecretStr("abc"), cursor=1))
    assert [s.text for s in segments if s.style is not None and s.style.reverse] == ["•"]


def test_an_empty_secret_shows_no_mask() -> None:
    assert "•" not in run_solo(SecretInput("Token")).frame


# --- numbers ----------------------------------------------------------------------


def test_numbers_accept_only_characters_a_number_holds() -> None:
    run = run_solo(NumberInput("Timeout"), "1", "a", "2", ".", "e", "x", "-", Paste("34\n"))

    assert field_of(run).text == "12-34"
    assert field_of(run_solo(NumberInput("Rate", integer=False), "1", ".", "5")).text == "1.5"
    assert field_of(run_solo(NumberInput("Rate", integer=False), "1", ".", "5")).value == 1.5


def test_number_bounds_and_parse_errors() -> None:
    bounded = NumberInput("Timeout", "30", minimum=1, maximum=600)
    assert bounded.validate() == ""
    assert bounded.value == 30
    assert NumberInput("Timeout", "700", minimum=1, maximum=600).validate() == (
        "Must be between 1 and 600."
    )
    assert NumberInput("Timeout", "0", minimum=1, maximum=600).validate() == (
        "Must be between 1 and 600."
    )
    assert NumberInput("N", "0", minimum=1).validate() == "Must be at least 1."
    assert NumberInput("N", "9", maximum=5).validate() == "Must be at most 5."
    assert (
        NumberInput(
            "N",
            "-",
        ).validate()
        == "Must be a whole number."
    )
    assert NumberInput("N", "1.5", integer=False, minimum=2).validate() == "Must be at least 2."
    assert NumberInput("N", "1-2", integer=False).validate() == "Must be a number."
    assert NumberInput("N", "nan", integer=False).value is None
    assert (
        NumberInput("N", "").validate() == ""
    )  # empty is valid; the form decides if it is required
    assert NumberInput("N", "").value is None
    assert NumberInput("N", "0.5", integer=False, maximum=1.5, minimum=0.25).validate() == ""
    assert NumberInput("N", "9", integer=False, minimum=0.5, maximum=1.5).validate() == (
        "Must be between 0.5 and 1.5."
    )


def test_a_required_number_is_invalid_when_empty_and_an_optional_one_is_not() -> None:
    assert NumberInput("N").validate() == ""
    assert NumberInput("N", required=True).validate() == "Enter a whole number."
    assert NumberInput("N", integer=False, required=True).validate() == "Enter a number."
    assert NumberInput("N", text="  ", required=True).validate() != ""
    assert NumberInput("N", text="3", required=True).validate() == ""
    assert NumberInput("N", required=True).value is None


def test_a_number_outside_an_exclusive_bound_is_invalid() -> None:
    low = NumberInput("N", text="0", integer=False, above=0)
    assert low.validate() == "Must be greater than 0."
    assert NumberInput("N", text="0.1", integer=False, above=0).validate() == ""
    high = NumberInput("N", text="10", integer=False, below=10)
    assert high.validate() == "Must be less than 10."
    assert NumberInput("N", text="9.9", integer=False, below=10).validate() == ""


def test_a_number_error_is_drawn_and_cleared_by_typing() -> None:
    component = NumberInput("Timeout", "700", minimum=1, maximum=600)
    run = run_solo(component.with_error(component.validate()), width=40)
    assert "Must be between 1 and 600." in run.frame

    run = run_solo(component.with_error(component.validate()), "backspace")
    assert "Must be between" not in run.frame


# --- themes -----------------------------------------------------------------------


def _gallery() -> list[object]:
    return [
        TextInput("Base URL", "https://example.com", help="The address.", placeholder="url"),
        _typed(TextInput("Fruit", complete=_fruit), "a"),
        _typed(PathInput("Path"), "/"),
        SecretInput("Token", SecretStr("secret")),
        NumberInput("Timeout", "30", minimum=1, maximum=600),
        TextInput("Bad", "x").with_error("Nope."),
    ]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", [(100, 30), (60, 20), (24, 10)])
def test_every_builtin_theme_renders_the_text_components(theme: str, size: tuple[int, int]) -> None:
    for component in _gallery():
        for focused in (True, False):
            run = run_solo(component, theme=BUILTIN_THEMES[theme], size=size, focused=focused)
            assert all(len(line) <= size[0] for line in run.frame.splitlines())


def test_plain_text_component_frames_are_pure_ascii() -> None:
    for component in _gallery():
        frame = run_solo(component, theme=BUILTIN_THEMES["plain"]).frame
        assert frame.isascii(), frame


def test_without_a_box_a_field_is_its_label_value_and_help_lines() -> None:
    run = run_solo(
        TextInput("Base URL", "https://x.test", help="The address."), theme=BUILTIN_THEMES["quiet"]
    )

    assert lines(run.frame) == ["Base URL", "https://x.test", "The address."]


def test_a_box_free_error_replaces_the_help_and_names_the_label_in_the_error_style() -> None:
    theme = BUILTIN_THEMES["quiet"]
    component = TextInput("Name", "x", help="Help.").with_error("Nope.")
    segments = render_styled(component.view(Frame(40, 10, theme), width=40), width=40)

    assert style_of(segments, "Nope.").color == role(theme, "screen.error").color
    assert style_of(segments, "Name").color == role(theme, "screen.error").color


def test_the_text_components_are_marked_experimental() -> None:
    for cls in (TextInput, PathInput, SecretInput, NumberInput):
        assert isinstance(function_mark(cls), Experimental)


def test_a_number_moves_its_caret_and_edits_in_place() -> None:
    run = run_solo(NumberInput("N", "123"), "left", "left", "9", "right", "home", "delete")

    assert field_of(run).text == "923"
    assert field_of(run).cursor == 0
    moved = run_solo(NumberInput("N", "123"), "left")
    assert (field_of(moved).text, field_of(moved).cursor) == ("123", 2)


def test_other_keys_while_a_completion_list_is_open_still_edit_the_text() -> None:
    run = run_solo(TextInput("Fruit", complete=_fruit), "a", "left", "ctrl-r", "p")

    assert field_of(run).value == "pa"
    assert field_of(run).cursor == 1
    assert field_of(run).completing is False  # nothing starts with "pa"


def test_a_box_free_field_without_a_label_draws_only_its_value() -> None:
    run = run_solo(TextInput("", "just value"), theme=BUILTIN_THEMES["quiet"])

    assert lines(run.frame) == ["just value"]


# --- review fixes -----------------------------------------------------------------


def test_a_prefilled_path_is_left_alone_by_tab_and_the_screen_moves_on(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir()
    for prefilled in (str(tmp_path), f"{tmp_path}/chi", f"{tmp_path}/"):
        run = run_solo(PathInput("Path", prefilled), "tab")

        assert field_of(run).value == prefilled  # not rewritten to "dir/" or a candidate
        assert run.model.unhandled == (NextField(),)
        assert field_of(run).completing is False
        assert "child" not in run.frame  # no list until the user edits


def test_the_list_opens_on_the_first_edit_of_a_prefilled_path_and_tab_then_accepts(
    tmp_path: Path,
) -> None:
    (tmp_path / "child").mkdir()
    run = run_solo(PathInput("Path", f"{tmp_path}/chi"), "l", "tab")

    assert field_of(run).value == f"{tmp_path}/child/"
    assert run.model.unhandled == ()


def test_a_prefilled_text_input_with_a_completer_is_not_rewritten_by_tab() -> None:
    run = run_solo(TextInput("Fruit", "ap", complete=_fruit), "tab")

    assert field_of(run).value == "ap"
    assert run.model.unhandled == (NextField(),)


def test_candidates_computed_for_other_text_are_never_offered_or_accepted() -> None:
    opened = _typed(TextInput("Fruit", complete=_fruit), "a")
    assert opened.completing

    for value in ("zzz", "", "ap", "a "):
        stale = replace(opened, value=value)
        run = run_solo(stale, "tab")

        assert stale.completing is False
        assert field_of(run).value == value  # the stale candidate did not replace it
        assert run.model.unhandled == (NextField(),)
        assert "apple" not in run_solo(stale).frame
    assert "apple" in run_solo(opened).frame


def test_a_choice_past_the_candidates_is_held_to_the_last_one() -> None:
    opened = _typed(TextInput("Fruit", complete=_fruit), "a")

    assert replace(opened, choice=9).update(Key("tab"))[0].value == "apricot"
    assert replace(opened, choice=9).update(Key("down"))[0].choice == 1
    assert replace(opened, choice=9).update(Key("up"))[0].choice == 0


def test_a_number_paste_is_all_or_nothing() -> None:
    assert field_of(run_solo(NumberInput("N", integer=False), Paste("1.5"))).text == "1.5"
    assert field_of(run_solo(NumberInput("N"), Paste("1.5"))).text == ""  # not "15"
    assert field_of(run_solo(NumberInput("N", integer=False), Paste("1e3"))).text == "1e3"
    assert field_of(run_solo(NumberInput("N"), Paste("1e3"))).text == ""  # not "13"
    assert field_of(run_solo(NumberInput("N"), "7", Paste("1,000"))).text == "7"  # not "71000"
    assert field_of(run_solo(NumberInput("N"), Paste(" 42\n"))).text == "42"  # surroundings go
    assert field_of(run_solo(NumberInput("N"), "7", Paste("1\n2"))).text == "7"
    untouched = NumberInput("N", "7")
    assert untouched.update(Paste("x"))[0] is untouched


def test_a_number_is_valid_at_exactly_its_inclusive_bounds() -> None:
    for text in ("1", "600"):
        assert NumberInput("T", text, minimum=1, maximum=600).validate() == ""
    assert NumberInput("T", "0", minimum=1, maximum=600).validate() != ""
    assert NumberInput("T", "601", minimum=1, maximum=600).validate() != ""
    for text in ("0.25", "1.5"):
        assert NumberInput("T", text, integer=False, minimum=0.25, maximum=1.5).validate() == ""
    assert NumberInput("T", "5", minimum=5).validate() == ""
    assert NumberInput("T", "5", maximum=5).validate() == ""


def test_a_float_takes_an_exponent_and_an_integer_does_not() -> None:
    run = run_solo(NumberInput("N", integer=False), *"1e-3")
    assert (field_of(run).text, field_of(run).value) == ("1e-3", 0.001)
    run = run_solo(NumberInput("N", integer=False), *"2.5E+4")
    assert (field_of(run).text, field_of(run).value) == ("2.5E+4", 25000.0)
    assert field_of(run_solo(NumberInput("N"), *"1e3")).text == "13"
    assert NumberInput("N", "1e", integer=False).validate() == "Must be a number."
    assert NumberInput("N", "1e999", integer=False).value is None  # not finite


def test_secret_draws_exactly_one_mask_per_character() -> None:
    for count in (0, 1, 5, 12):
        secret = SecretStr("x" * count)
        assert run_solo(SecretInput("Token", secret)).frame.count("\u2022") == count
        plain = run_solo(SecretInput("Token", secret), theme=BUILTIN_THEMES["plain"])
        assert plain.frame.count("*") == count
    run = run_solo(SecretInput("Token"), *"abcd", "backspace")
    assert run.frame.count("\u2022") == 3


def test_an_edit_clears_the_error_of_a_secret_a_check_and_a_select() -> None:
    secret = SecretInput("Token").with_error("Needed.")
    assert secret.update(Key("left"))[0].error == "Needed."  # a caret move keeps it
    assert secret.update(Key("a"))[0].error == ""
    assert secret.update(Paste("abc"))[0].error == ""

    check = Check("Verify").with_error("Needed.")
    assert check.update(Key("x"))[0] is check
    assert check.update(Key(" "))[0].error == ""

    select = Select("Region", (ListItem("a", "A"), ListItem("b", "B")), "a").with_error("Needed.")
    opened = select.update(Key("enter"))[0]
    assert opened.error == "Needed."  # opening is not an edit
    assert opened.update(Key("down"))[0].update(Key("enter"))[0].error == ""


def test_ctrl_w_with_a_trailing_space_removes_the_word_and_the_space() -> None:
    run = run_solo(TextInput("Name", "one two "), "ctrl-w")
    assert (field_of(run).value, field_of(run).cursor) == ("one ", 4)
    run = run_solo(TextInput("Name", "one two   "), "ctrl-w", "ctrl-w")
    assert field_of(run).value == ""
