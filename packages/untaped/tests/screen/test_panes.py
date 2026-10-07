"""``Panes``: two bordered panes side by side or stacked, with focus moving between them."""

from __future__ import annotations

import pytest

from screen.gallery import field_of, lines, render_styled, role, run_solo, style_of
from untaped.screen.components.buttons import Button, Buttons, Pressed
from untaped.screen.components.choices import ListItem, SingleList
from untaped.screen.components.form import Form
from untaped.screen.components.inputs import TextInput
from untaped.screen.components.layout import MIN_PANE_WIDTH, WIDE, Panes
from untaped.screen.core import Frame, Key, NextField, PrevField
from untaped.stability import Experimental, function_mark
from untaped.theme import BUILTIN_THEMES

THEMES = sorted(BUILTIN_THEMES)
DEFAULT = BUILTIN_THEMES["default"]
CAPS = tuple(ListItem(name, name, detail=detail) for name, detail in (
    ("awx", "ok"), ("jira", "ok"), ("github", "missing token"), ("ansible", "none"),
))  # fmt: skip


def _panes(**kwargs: object) -> Panes:
    form = Form(
        (
            ("url", TextInput("Base URL", "https://api.github.com")),
            ("note", TextInput("Note")),
            ("buttons", Buttons((Button("save", "Save", "primary"), Button("cancel", "Cancel")))),
        )
    )
    return Panes(
        SingleList("", CAPS, "github", show_chosen=False),
        form,
        left_title="Capabilities",
        right_title="github",
        **kwargs,  # type: ignore[arg-type]
    )


def _styled(panes: Panes, *, focused: bool = True, width: int = 100, height: int = 30):
    return render_styled(
        panes.view(Frame(width, height, DEFAULT), focused=focused, width=width), width=width
    )


# --- layout ------------------------------------------------------------------------


def test_side_by_side_from_the_wide_width_and_stacked_below_it() -> None:
    side = run_solo(_panes(), size=(WIDE, 24)).frame
    stacked = run_solo(_panes(), size=(WIDE - 1, 24)).frame

    top = lines(side)[0]
    assert "Capabilities" in top
    assert "github" in top  # both titles on the first line
    assert "╮╭" in top
    stacked_lines = lines(stacked)
    assert "Capabilities" in stacked_lines[0]
    assert "github" not in stacked_lines[0]
    assert any(line.startswith("╭─ github") for line in stacked_lines)


def test_wide_is_one_hundred_columns() -> None:
    assert WIDE == 100


def test_the_left_pane_takes_its_share_of_the_width() -> None:
    top = lines(run_solo(_panes(), size=(100, 24)).frame)[0]
    assert top.index("╮") + 1 == 35  # 35% of 100

    even = lines(run_solo(_panes(split=0.5), size=(100, 24)).frame)[0]
    assert even.index("╮") + 1 == 50


def test_a_split_never_squeezes_either_pane_below_the_minimum_width() -> None:
    small = lines(run_solo(_panes(split=0.01), size=(100, 24)).frame)[0]
    assert small.index("╮") + 1 == MIN_PANE_WIDTH

    large = lines(run_solo(_panes(split=0.99), size=(100, 24)).frame)[0]
    assert large.index("╮") + 1 == 100 - MIN_PANE_WIDTH


def test_side_by_side_panes_are_equally_tall() -> None:
    rows = lines(run_solo(_panes(), size=(100, 30)).frame)
    bottoms = [number for number, line in enumerate(rows) if line.startswith("╰")]

    assert len(bottoms) == 1  # one bottom edge: both panes end on it
    assert rows[bottoms[0]].count("╯") == 2
    assert all(len(line) == 100 for line in rows if line.startswith(("│", "╭", "╰")))


def test_each_component_is_drawn_inside_its_pane() -> None:
    frame = run_solo(_panes(), size=(100, 30)).frame

    assert "│ awx" in frame
    assert "missing token" in frame
    assert "╭─ Base URL" in frame
    assert "https://api.github.com" in frame
    assert "│ Save │" in frame


def test_a_bare_pane_has_no_box_of_its_own_and_unbare_keeps_it() -> None:
    bare = run_solo(_panes(), size=(100, 30)).frame
    assert bare.count("╭") == 2 + 2 + 2  # the two panes, the form's two fields, two buttons

    boxed = run_solo(_panes(left_bare=False), size=(100, 30)).frame
    assert boxed.count("╭") == bare.count("╭") + 1

    bare_right = run_solo(_panes(right_bare=True), size=(100, 30)).frame
    assert "╭─ Base URL" not in bare_right


def test_stacked_panes_give_the_focused_one_more_rows() -> None:
    left_focused = lines(run_solo(_panes(), size=(60, 30)).frame)
    right_focused = lines(run_solo(_panes(focus=1), size=(60, 30)).frame)

    def first_height(rows: list[str]) -> int:
        return next(number for number, line in enumerate(rows) if line.startswith("╰")) + 1

    assert first_height(left_focused) > first_height(right_focused)


def test_a_long_component_in_a_pane_is_windowed_to_the_pane() -> None:
    many = SingleList("", tuple(ListItem(f"n{n}", f"item {n}") for n in range(50)), cursor=40)
    run = run_solo(Panes(many, TextInput("T"), left_title="L"), size=(100, 12))

    assert "item 40" in run.frame
    assert "item 0" not in run.frame
    assert len(run.frame.splitlines()) <= 12


# --- focus -------------------------------------------------------------------------


def test_the_focused_pane_has_the_focus_border_and_a_bright_title() -> None:
    focus = role(DEFAULT, "screen.focus").color
    border = role(DEFAULT, "screen.border").color

    def edges(panes: Panes, *, focused: bool = True) -> list:
        return [
            s.style.color
            for s in _styled(panes, focused=focused)
            if s.text in ("╭─", "╰") or s.text.startswith("╰─")
        ][:2]

    assert edges(_panes()) == [focus, border]
    assert edges(_panes(focus=1)) == [border, focus]
    assert edges(_panes(), focused=False) == [border, border]
    accent = role(DEFAULT, "screen.accent")
    assert style_of(_styled(_panes()), "Capabilities").bold == accent.bold
    assert style_of(_styled(_panes()), "Capabilities").color == accent.color
    assert style_of(_styled(_panes()), "github").color == role(DEFAULT, "screen.value").color


def test_only_the_focused_pane_draws_its_cursor_and_caret() -> None:
    fill = role(DEFAULT, "screen.highlight").bgcolor

    def filled(panes: Panes) -> bool:
        return any(s.style is not None and s.style.bgcolor == fill for s in _styled(panes))

    assert filled(_panes())  # the list's cursor row
    assert not filled(_panes(focus=1))


# --- keys --------------------------------------------------------------------------


def test_tab_moves_between_the_panes_through_the_right_form_and_back() -> None:
    def focus_of(*keys: str) -> tuple[int, int]:
        field = field_of(run_solo(_panes(), *keys))
        return field.focus, field.right.focus

    assert focus_of() == (0, 0)
    assert focus_of("tab") == (1, 0)  # the list passes on tab: to the right pane
    assert focus_of("tab", "tab") == (1, 1)  # inside the form now
    assert focus_of("tab", "tab", "tab") == (1, 2)
    assert focus_of("tab", "tab", "tab", "tab") == (0, 2)  # the form's last field passed it on
    assert focus_of("shift-tab") == (1, 0)
    assert focus_of("tab", "shift-tab") == (0, 0)  # the first field passes it back


def test_keys_go_only_to_the_focused_pane() -> None:
    right = run_solo(_panes(), "tab", "x")
    assert field_of(right).right.value["url"] == "https://api.github.comx"
    assert field_of(right).left.cursor == 2

    left = run_solo(_panes(), "down", "x")
    assert field_of(left).left.cursor == 3
    assert field_of(left).right.value["url"] == "https://api.github.com"


def test_a_child_that_consumes_tab_keeps_it() -> None:
    panes = Panes(TextInput("A", complete=lambda text: ["apple"] * (text == "a")), TextInput("B"))

    first = run_solo(panes, "a", "tab")  # completions open once the text is edited
    assert field_of(first).focus == 0  # the completion took the tab
    assert field_of(first).left.value == "apple"
    assert field_of(run_solo(panes, "a", "tab", "tab")).focus == 1


def test_a_command_from_a_pane_reaches_the_screen() -> None:
    pressed = run_solo(_panes(), "tab", "tab", "tab", "enter")  # into the form, to the buttons

    assert pressed.model.seen == (Pressed("save"),)
    assert field_of(pressed).focus == 1


def test_unused_messages_return_the_same_object() -> None:
    panes = _panes(focus=1)

    assert panes.update(Key("up"))[0] is panes
    assert panes.update(Key("ctrl-r"))[0] is panes
    assert panes.update("text")[0] is panes
    assert _panes().update(Key("ctrl-r"))[0].focus == 0
    assert panes.update(NextField())[0].focus == 1  # the form uses it: its next field
    assert panes.update(PrevField())[0].focus == 0  # the form is at its first field: the list


def test_the_focus_is_either_pane() -> None:
    assert _panes(focus=7).focus == 1
    assert _panes(focus=-2).focus == 0


# --- the component contract --------------------------------------------------------


def test_value_validate_and_error_follow_the_two_components() -> None:
    panes = _panes()

    assert set(panes.value) == {"left", "right"}
    assert panes.value["left"] == "github"
    assert panes.validate() == ""
    bad = Panes(TextInput("A", validator=lambda _: "Left bad."), TextInput("B"))
    assert bad.validate() == "Left bad."
    worse = Panes(TextInput("A"), TextInput("B", validator=lambda _: "Right bad."))
    assert worse.validate() == "Right bad."


def test_an_error_shows_under_the_panes_in_the_error_colour() -> None:
    segments = _styled(_panes().with_error("Check failed."))

    assert style_of(segments, "Check failed.").color == role(DEFAULT, "screen.error").color
    assert _panes().with_error("").error == ""


# --- themes and sizes --------------------------------------------------------------


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", [(100, 30), (99, 30), (60, 20), (24, 10), (12, 4), (4, 3), (1, 1)])
def test_narrow_and_short_terminals_do_not_raise(theme: str, size: tuple[int, int]) -> None:
    for focus in (0, 1):
        run = run_solo(_panes(focus=focus), size=size, theme=BUILTIN_THEMES[theme])
        assert all(len(line) <= size[0] for line in run.frame.splitlines())


def test_plain_panes_are_pure_ascii_and_use_the_ascii_box() -> None:
    for size in ((100, 30), (60, 30)):
        frame = run_solo(
            _panes().with_error("Nope."), size=size, theme=BUILTIN_THEMES["plain"]
        ).frame
        assert frame.isascii()
        assert "+- Capabilities" in frame


def test_without_a_box_the_panes_are_titles_over_their_content() -> None:
    side = run_solo(_panes(), size=(100, 20), theme=BUILTIN_THEMES["quiet"]).frame
    assert lines(side)[0].startswith("Capabilities")
    assert "github" in lines(side)[0]
    assert not any(char in side for char in "╭│╰")

    stacked = lines(run_solo(_panes(), size=(60, 30), theme=BUILTIN_THEMES["quiet"]).frame)
    assert stacked[0] == "Capabilities"
    assert "github" in stacked


def test_panes_are_marked_experimental() -> None:
    assert isinstance(function_mark(Panes), Experimental)
