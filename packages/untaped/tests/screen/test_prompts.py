"""The one-shot prompts as inline screens, driven without a terminal."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

from untaped.prompts import PromptChoice
from untaped.screen.core import Cancel, Paste, Quit, Resize, Screen
from untaped.screen.prompts import (
    PROMPT_ALTERNATIVE,
    PROMPT_COMMAND,
    confirm_screen,
    multiselect_screen,
    secret_screen,
    select_screen,
    text_screen,
)
from untaped.testing import drive_screen
from untaped.theme import BUILTIN_THEMES

CHOICES = [
    PromptChoice(("repo", 1), "Alpha", "the first"),
    PromptChoice(("repo", 2), "Beta"),
    PromptChoice(("repo", 3), "Gamma"),
]
THEMES = sorted(BUILTIN_THEMES)


def _drive(screen: Screen[Any, Any], *keys: Any, theme: str = "default", size=(100, 30)):
    return drive_screen(screen, keys, theme=BUILTIN_THEMES[theme], size=size)


def _all_screens() -> list[Callable[[], Screen[Any, Any]]]:
    return [
        lambda: text_screen("Name", "dev"),
        lambda: secret_screen("Token", confirmation=True),
        lambda: select_screen("Pick", CHOICES, CHOICES[1].value),
        lambda: select_screen("Pick", CHOICES, None, search=True),
        lambda: multiselect_screen("Pick many", CHOICES, [CHOICES[0].value]),
        lambda: confirm_screen("Continue?", True),
    ]


# --- every prompt -----------------------------------------------------------------


@pytest.mark.parametrize("build", _all_screens())
def test_a_prompt_is_a_small_inline_screen_with_no_command_of_its_own(
    build: Callable[[], Screen[Any, Any]],
) -> None:
    screen = build()

    assert screen.layout == "inline"
    assert (screen.command, screen.alternative) == (PROMPT_COMMAND, PROMPT_ALTERNATIVE)


@pytest.mark.parametrize("build", _all_screens())
def test_esc_cancels_and_ctrl_c_interrupts(build: Callable[[], Screen[Any, Any]]) -> None:
    assert _drive(build(), "esc").outcome == Cancel()
    assert _drive(build(), "ctrl-c").outcome == Cancel(interrupted=True)


@pytest.mark.parametrize("build", _all_screens())
def test_ctrl_c_in_the_help_overlay_interrupts(build: Callable[[], Screen[Any, Any]]) -> None:
    run = _drive(build(), "?", "ctrl-c")
    assert run.outcome == Cancel(interrupted=True)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("build", _all_screens())
def test_every_builtin_theme_draws_every_prompt(
    build: Callable[[], Screen[Any, Any]], theme: str
) -> None:
    run = _drive(build(), "down", "x", theme=theme)

    assert all(frame.strip() for frame in run.frames)
    assert "esc back" in run.frames[0]


@pytest.mark.parametrize("build", _all_screens())
def test_the_plain_theme_draws_pure_ascii(build: Callable[[], Screen[Any, Any]]) -> None:
    run = _drive(build(), "down", "x", "enter", theme="plain")

    assert all(frame.isascii() for frame in run.frames)


@pytest.mark.parametrize("build", _all_screens())
def test_a_prompt_without_a_border_is_its_label_and_value_lines(
    build: Callable[[], Screen[Any, Any]],
) -> None:
    boxless = BUILTIN_THEMES["default"].model_copy(update={"border": "none"})
    run = drive_screen(build(), [], theme=boxless)

    assert not set("╭╮╰╯│") & set(run.frames[0])


@pytest.mark.parametrize("build", _all_screens())
def test_a_narrow_terminal_still_draws(build: Callable[[], Screen[Any, Any]]) -> None:
    run = _drive(build(), Resize(24, 10), "x", size=(24, 10))

    assert run.frame.strip()


def test_a_wide_terminal_does_not_stretch_the_box() -> None:
    frame = _drive(text_screen("Name"), size=(200, 30)).frames[0]

    assert max(len(line) for line in frame.splitlines()[:3]) == 72


def test_the_question_is_the_label_of_the_box() -> None:
    first_line = _drive(text_screen("Profile name")).frames[0].splitlines()[0]

    assert first_line.startswith("╭─ Profile name ")


def test_a_question_too_long_for_the_border_sits_above_an_unlabelled_box() -> None:
    question = "Which of the many workspaces should this repository be added to right now?"
    lines = _drive(text_screen(question)).frames[0].splitlines()

    assert lines[0].startswith(question[:40])
    assert lines[1].startswith("╭─")
    assert question not in lines[1]


def test_a_question_over_several_lines_is_not_cut_into_the_border() -> None:
    lines = _drive(confirm_screen("Delete these?\n  - a\n  - b", False)).frames[0].splitlines()

    assert lines[:3] == ["Delete these?", "  - a", "  - b [y/N]"]
    assert lines[3].startswith("╭─")


# --- text -----------------------------------------------------------------------


def test_text_shows_the_default_and_enter_returns_it() -> None:
    run = _drive(text_screen("Name", "dev"), "enter")

    assert "│ dev " in run.frames[0]
    assert run.result == "dev"


def test_text_returns_exactly_what_was_typed_without_stripping() -> None:
    run = _drive(text_screen("Name"), " ", "a", " ", "b", " ", "enter")

    assert run.result == " a b "


def test_text_edits_the_default_and_takes_a_paste() -> None:
    run = _drive(text_screen("Name", "dev"), "backspace", "x", Paste("-1"), "enter")

    assert run.result == "de" + "x-1"


def test_text_without_a_default_starts_empty_and_enter_returns_the_empty_string() -> None:
    assert _drive(text_screen("Name", None), "enter").result == ""


def test_a_question_mark_in_text_is_text_not_help() -> None:
    run = _drive(text_screen("URL"), "?", "enter")

    assert run.result == "?"


def test_ctrl_d_on_an_empty_answer_cancels_but_deletes_nothing_on_a_filled_one() -> None:
    assert _drive(text_screen("Name"), "ctrl-d").outcome == Cancel()

    run = _drive(text_screen("Name", "x"), "ctrl-d", "enter")
    assert run.result == "x"


def test_the_footer_shows_ctrl_d_only_while_it_would_cancel() -> None:
    run = _drive(text_screen("Name"), "a")

    assert "ctrl-d cancel" in run.frames[0]
    assert "ctrl-d" not in run.frames[1]


# --- secret ---------------------------------------------------------------------


def test_secret_returns_what_was_typed_as_a_secret_str() -> None:
    run = _drive(secret_screen("Token"), *"hunter2", "enter")

    value, repeated = run.result
    assert (value.get_secret_value(), repeated.get_secret_value()) == ("hunter2", "hunter2")


def test_a_secret_is_masked_and_never_in_a_frame_or_a_repr() -> None:
    secret = "s3cr3t-t0ken"
    run = _drive(
        secret_screen("Token", confirmation=True),
        *secret,
        "enter",
        Paste(secret),
        "enter",
    )

    assert any("•" in frame for frame in run.frames)
    for frame in run.frames:
        assert "s3cr3t" not in frame
        assert "t0ken" not in frame
    assert "s3cr3t" not in repr(run) + repr(run.model) + repr(run.result)


def test_secret_confirmation_asks_twice_and_returns_both_for_the_caller_to_compare() -> None:
    run = _drive(secret_screen("Token", confirmation=True), *"abc", "enter", *"abd", "enter")

    assert run.outcome is not None
    value, repeated = run.result
    assert (value.get_secret_value(), repeated.get_secret_value()) == ("abc", "abd")
    assert "Confirm value" in run.frames[0]


def test_secret_without_confirmation_has_one_box() -> None:
    assert "Confirm value" not in _drive(secret_screen("Token")).frames[0]


def test_enter_in_the_first_box_moves_to_the_confirmation_and_tab_moves_between_them() -> None:
    run = _drive(secret_screen("Token", confirmation=True), "a", "enter")
    assert run.outcome is None  # still open: the second box is next

    run = _drive(secret_screen("Token", confirmation=True), "a", "tab", "b", "shift-tab", "c")
    assert run.model.first.value.get_secret_value() == "ac"
    assert run.model.second.value.get_secret_value() == "b"


def test_tab_does_nothing_without_a_confirmation_box() -> None:
    run = _drive(secret_screen("Token"), "a", "tab", "b", "enter")

    assert run.result[0].get_secret_value() == "ab"


def test_ctrl_d_cancels_an_empty_secret_only() -> None:
    assert _drive(secret_screen("Token"), "ctrl-d").outcome == Cancel()
    assert _drive(secret_screen("Token"), "a", "ctrl-d").outcome is None


# --- select ---------------------------------------------------------------------


def test_select_marks_the_default_and_starts_the_cursor_on_it() -> None:
    run = _drive(select_screen("Pick", CHOICES, CHOICES[1].value), "enter")

    assert "▶ Beta" in run.frames[0]
    assert re.search(r"  Alpha +the first", run.frames[0])  # the description is the row's detail
    assert run.result == ("repo", 2)  # enter answers with the row under the cursor


def test_select_moves_the_cursor_and_returns_the_typed_value_of_that_row() -> None:
    assert _drive(select_screen("Pick", CHOICES, None), "down", "down", "enter").result == (
        "repo",
        3,
    )
    assert _drive(select_screen("Pick", CHOICES, None), "enter").result == ("repo", 1)
    assert _drive(select_screen("Pick", CHOICES, CHOICES[2].value), "up", "enter").result == (
        "repo",
        2,
    )


def test_select_with_an_unknown_default_marks_nothing() -> None:
    run = _drive(select_screen("Pick", CHOICES, ("repo", 99)), "enter")

    assert "▶" not in run.frames[0]
    assert run.result == ("repo", 1)


def test_select_cancels_on_ctrl_d() -> None:
    assert _drive(select_screen("Pick", CHOICES, None), "ctrl-d").outcome == Cancel()


def test_search_select_filters_as_you_type_and_enter_takes_the_best_match() -> None:
    run = _drive(select_screen("Pick", CHOICES, None, search=True), *"gam", "enter")

    assert "Gamma" in run.frames[3]
    assert "Alpha" not in run.frames[3]
    assert run.result == ("repo", 3)


def test_search_select_also_matches_the_description() -> None:
    run = _drive(select_screen("Pick", CHOICES, None, search=True), *"first", "enter")

    assert run.result == ("repo", 1)


def test_search_select_starts_on_the_default_and_enter_takes_it() -> None:
    run = _drive(select_screen("Pick", CHOICES, CHOICES[1].value, search=True), "enter")

    assert "▶ Beta" in run.frames[0]
    assert run.result == ("repo", 2)


def test_search_select_with_no_match_stays_open() -> None:
    run = _drive(select_screen("Pick", CHOICES, None, search=True), *"zzz", "enter")

    assert run.outcome is None
    assert "0 of 3" in run.frame


def test_search_select_esc_clears_the_query_before_it_cancels() -> None:
    run = _drive(select_screen("Pick", CHOICES, None, search=True), "g", "esc")
    assert run.outcome is None
    assert run.model.field.query == ""

    assert _drive(select_screen("Pick", CHOICES, None, search=True), "g", "esc", "esc").outcome == (
        Cancel()
    )


def test_search_select_ctrl_d_waits_while_a_query_is_typed() -> None:
    assert _drive(select_screen("Pick", CHOICES, None, search=True), "g", "ctrl-d").outcome is None
    assert _drive(select_screen("Pick", CHOICES, None, search=True), "ctrl-d").outcome == Cancel()


# --- multiselect ----------------------------------------------------------------


def test_multiselect_preselects_the_defaults() -> None:
    run = _drive(multiselect_screen("Pick many", CHOICES, [CHOICES[2].value]), "enter")

    assert re.search(r"\[ \] Alpha +the first", run.frames[0])
    assert "[✓] Gamma" in run.frames[0]
    assert run.result == [("repo", 3)]


def test_multiselect_toggles_with_space_and_returns_the_checked_values_in_order() -> None:
    run = _drive(
        multiselect_screen("Pick many", CHOICES, []),
        "down", "down", " ", "up", "up", " ", "enter",
    )  # fmt: skip

    assert run.result == [("repo", 1), ("repo", 3)]


def test_multiselect_can_end_with_nothing_checked() -> None:
    run = _drive(multiselect_screen("Pick many", CHOICES, [CHOICES[0].value]), " ", "enter")

    assert run.result == []


def test_multiselect_names_space_in_the_footer_and_cancels_on_ctrl_d() -> None:
    run = _drive(multiselect_screen("Pick many", CHOICES, []), "ctrl-d")

    assert "space toggle" in run.frames[0]
    assert run.outcome == Cancel()


# --- confirm --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keys", "default", "expected"),
    [
        (["enter"], True, True),
        (["enter"], False, False),
        (["y", "enter"], False, True),
        (["n", "enter"], True, False),
        (["Y", "E", "S", "enter"], False, True),
        (["N", "o", "enter"], True, False),
        ([" ", "y", " ", "enter"], False, True),
    ],
)
def test_confirm_takes_the_default_or_a_typed_answer(
    keys: list[str], default: bool, expected: bool
) -> None:
    run = _drive(confirm_screen("Remove alias?", default), *keys)

    assert isinstance(run.outcome, Quit)
    assert run.result is expected


def test_confirm_shows_which_answer_enter_takes() -> None:
    assert "Remove alias? [Y/n]" in _drive(confirm_screen("Remove alias?", True)).frames[0]
    assert "Remove alias? [y/N]" in _drive(confirm_screen("Remove alias?", False)).frames[0]


def test_confirm_with_an_unknown_answer_says_so_and_stays_until_a_valid_one() -> None:
    run = _drive(confirm_screen("Continue?", False), "m", "a", "enter")

    assert run.outcome is None
    assert "Please answer y or n." in run.frame

    run = _drive(confirm_screen("Continue?", False), "m", "enter", "backspace", "y", "enter")
    assert "Please answer y or n." in run.frames[2]
    assert "Please answer y or n." not in run.frames[4]  # editing clears it
    assert run.result is True


def test_a_prefilled_answer_is_not_offered() -> None:
    # An "n" in the box would turn a typed "y" into "ny" and ask again.
    assert "│ n" not in _drive(confirm_screen("Continue?", False)).frames[0]
    assert _drive(confirm_screen("Continue?", False), "y", "enter").result is True


def test_ctrl_d_cancels_an_empty_confirmation_only() -> None:
    assert _drive(confirm_screen("Continue?", True), "ctrl-d").outcome == Cancel()
    assert _drive(confirm_screen("Continue?", True), "y", "ctrl-d").outcome is None
