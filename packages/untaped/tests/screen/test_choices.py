"""The choice components: Select, SingleList, MultiList, Check and Cycle."""

from __future__ import annotations

import pytest

from screen.gallery import field_of, lines, render_styled, role, run_solo, style_of
from untaped.screen.components.choices import Check, Cycle, ListItem, MultiList, Select, SingleList
from untaped.screen.core import Activate, Cancel, Frame, Key
from untaped.stability import Experimental, function_mark
from untaped.theme import BUILTIN_THEMES

THEMES = sorted(BUILTIN_THEMES)
DEFAULT = BUILTIN_THEMES["default"]
REGIONS = tuple(
    ListItem(name, name) for name in ("us-east", "us-west", "eu-central", "ap-southeast")
)
OUTPUTS = tuple(ListItem(name, name) for name in ("table", "json", "yaml"))


def _segments(component: object, theme: str = "default", *, focused: bool = True, width: int = 46):
    return render_styled(
        component.view(Frame(60, 20, BUILTIN_THEMES[theme]), focused=focused, width=width),  # type: ignore[attr-defined]
        width=width,
    )


def _row(segments: list, needle: str, *, after: int = 0) -> list:
    """The segments of the first rendered line from line ``after`` on that holds ``needle``."""
    line: list = []
    number = 0
    for segment in segments:
        line.append(segment)
        if segment.text == "\n":
            if number >= after and any(needle in part.text for part in line):
                return line
            line = []
            number += 1
    raise AssertionError(f"no line from {after} holds {needle!r}")


# --- Select -----------------------------------------------------------------------


def test_a_closed_select_shows_the_value_and_the_expand_symbol() -> None:
    run = run_solo(Select("Region", REGIONS, "us-west"))

    assert lines(run.frame)[1].startswith("│ us-west")
    assert lines(run.frame)[1].endswith("▼ │")
    assert "us-east" not in run.frame  # closed: only the value


def test_enter_opens_a_select_before_submitting() -> None:
    run = run_solo(Select("Region", REGIONS, "us-east"), "enter")

    assert field_of(run).open
    assert run.model.unhandled == ()  # no Activate reached the screen
    assert "▲" in run.frame
    assert all(name in run.frame for name in ("us-west", "eu-central", "ap-southeast"))


def test_esc_closes_an_open_select_first() -> None:
    run = run_solo(Select("Region", REGIONS, "us-east"), "enter", "esc")
    assert not field_of(run).open
    assert run.outcome is None
    assert field_of(run).value == "us-east"

    run = run_solo(Select("Region", REGIONS, "us-east"), "enter", "esc", "esc")
    assert run.outcome == Cancel()  # closed now: Back reaches the runtime

    assert run_solo(Select("Region", REGIONS), "esc").outcome == Cancel()


def test_up_and_down_move_and_enter_picks_and_closes() -> None:
    run = run_solo(Select("Region", REGIONS, "us-east"), "enter", "down", "down", "enter")

    assert field_of(run).value == "eu-central"
    assert not field_of(run).open
    assert run.model.unhandled == ()  # the picking enter was consumed
    assert "eu-central" in lines(run.frame)[1]

    run = run_solo(Select("Region", REGIONS, "us-east"), "enter", "enter", "enter")
    assert field_of(run).open  # pick (same value), then the third enter opens it again


def test_enter_on_a_closed_select_with_nothing_to_open_is_left_to_the_form() -> None:
    run = run_solo(Select("Region", ()), "enter")

    assert run.model.unhandled == (Activate(),)


def test_select_cursor_starts_on_the_value_and_clamps_at_the_ends() -> None:
    select = Select("Region", REGIONS, "eu-central", open=True)
    assert select.cursor == 2
    assert select.update(Key("up"))[0].cursor == 1
    first = Select("Region", REGIONS, "us-east", open=True)
    assert first.update(Key("up"))[0] is first
    last = Select("Region", REGIONS, "ap-southeast", open=True)
    assert last.update(Key("down"))[0] is last
    assert last.update(Key("home"))[0].cursor == 0
    assert first.update(Key("end"))[0].cursor == 3


def test_select_marks_the_chosen_value_and_highlights_the_cursor() -> None:
    select = Select("Region", REGIONS, "us-east", open=True, cursor=2)
    segments = _segments(select)

    chosen = style_of(_row(segments, "us-east", after=3), "▶")  # after the header and the rule
    assert chosen.color == role(DEFAULT, "screen.success").color
    highlight = role(DEFAULT, "screen.highlight")
    cursor_style = style_of(_row(segments, "eu-central"), "eu-central")
    assert cursor_style.bgcolor == highlight.bgcolor  # the one fill
    assert cursor_style.color == highlight.color  # bright text
    assert cursor_style.bold
    other = style_of(_row(segments, "us-west"), "us-west")
    assert other.bgcolor is None  # no fill anywhere else
    assert other.color == role(DEFAULT, "screen.muted").color
    chosen_row = style_of(_row(segments, "us-east", after=3), "us-east")
    assert chosen_row.bold
    assert not any(
        s.style is not None and s.style.bgcolor is not None
        for s in _segments(Select("Region", REGIONS, "us-east"))
    )  # a closed select has no fill at all


def test_an_unfocused_open_select_is_drawn_closed() -> None:
    run = run_solo(Select("Region", REGIONS, "us-east", open=True), focused=False)

    assert "▼" in run.frame
    assert "us-west" not in run.frame


def test_a_long_select_shows_a_window_that_keeps_the_cursor_row() -> None:
    many = tuple(ListItem(f"item{n:02d}", f"item{n:02d}") for n in range(40))
    run = run_solo(Select("Item", many, "item00"), "enter", *["down"] * 25, size=(60, 30))

    assert "item25" in run.frame
    assert "item01" not in run.frame  # item00 is the header's value; the window moved on
    assert sum("item" in line for line in run.frame.splitlines()) <= 9


def test_a_list_item_shows_its_detail_right_aligned_and_dims_when_dimmed() -> None:
    items = (
        ListItem("a", "alpha", detail="2 days", detail_role="screen.success"),
        ListItem("b", "beta", dimmed=True),
    )
    select = Select("X", items, "a", open=True, cursor=0)
    segments = _segments(select, width=30)

    assert any("alpha" in line and line.rstrip(" │").endswith("2 days") for line in
               "".join(s.text for s in segments).splitlines())  # fmt: skip
    beta = style_of(_row(segments, "beta"), "beta")
    assert beta.dim


def test_a_wide_label_is_cut_with_the_ellipsis_not_wrapped() -> None:
    items = (ListItem("a", "x" * 80),)
    run = run_solo(Select("X", items, "a", open=True), width=30)

    assert all(len(line) <= 60 for line in run.frame.splitlines())
    assert "…" in run.frame


def test_without_a_box_an_open_select_has_no_rule() -> None:
    run = run_solo(Select("Region", REGIONS, "us-east", open=True), theme=BUILTIN_THEMES["quiet"])

    body = lines(run.frame)
    assert body[0] == "Region"
    assert body[1].startswith("us-east")
    assert body[1].endswith("▲")
    assert body[2].startswith("▶ us-east")
    assert "─" not in run.frame


# --- SingleList -------------------------------------------------------------------


def test_single_list_marks_only_the_value() -> None:
    run = run_solo(SingleList("Output", OUTPUTS, "table", cursor=1))

    assert sum("▶" in line for line in lines(run.frame)) == 1
    table_line = next(line for line in lines(run.frame) if "table" in line)
    assert "▶ table" in table_line
    assert "▶ json" not in run.frame


def test_single_list_cursor_is_only_the_row_highlight_and_picking_moves_the_mark() -> None:
    component = SingleList("Output", OUTPUTS, "table", cursor=1)
    segments = _segments(component)
    highlight = role(DEFAULT, "screen.highlight")
    assert style_of(_row(segments, "json"), "json").bgcolor == highlight.bgcolor
    assert style_of(_row(segments, "table"), "table").bgcolor is None
    assert style_of(_row(segments, "table"), "▶").color == role(DEFAULT, "screen.success").color

    run = run_solo(component, " ")
    assert field_of(run).value == "json"
    run = run_solo(component, "down", "enter")
    assert field_of(run).value == "yaml"
    assert run.model.unhandled == ()


def test_enter_on_the_value_already_chosen_is_left_to_the_form() -> None:
    run = run_solo(SingleList("Output", OUTPUTS, "table", cursor=0), "enter")

    assert run.model.unhandled == (Activate(),)
    assert field_of(run).value == "table"


def test_single_list_hides_the_marker_column_when_asked() -> None:
    run = run_solo(SingleList("Output", OUTPUTS, "table", show_chosen=False))

    assert "▶" not in run.frame
    assert lines(run.frame)[1].startswith("│ table")


def test_an_unfocused_list_shows_no_cursor_row() -> None:
    segments = _segments(SingleList("Output", OUTPUTS, "table", cursor=1), focused=False)

    assert not any(s.style is not None and s.style.bgcolor is not None for s in segments)


def test_a_list_cursor_never_leaves_its_items() -> None:
    assert SingleList("O", OUTPUTS, "table", cursor=99).cursor == 2
    assert SingleList("O", (), "").cursor == 0
    component = SingleList("O", OUTPUTS, "table")
    assert component.update(Key("up"))[0] is component
    assert component.update(Key("down"))[0].cursor == 1


# --- MultiList --------------------------------------------------------------------


def test_multi_list_toggles_and_brackets() -> None:
    items = tuple(ListItem(name, name) for name in ("awx", "jira", "github", "ansible"))
    component = MultiList("Capabilities", items, frozenset({"awx"}), cursor=1)
    assert "[✓] awx" in run_solo(component).frame
    assert "[ ] jira" in run_solo(component).frame

    run = run_solo(component, " ", "down", "down", " ", " ")
    assert field_of(run).value == ("awx", "jira")  # items order, not toggle order
    assert field_of(run).selected == {"awx", "jira"}
    run = run_solo(component, " ")
    assert "[✓] jira" in run.frame
    run = run_solo(component, "up", " ")
    assert field_of(run).value == ()


def test_multi_list_leaves_enter_to_the_form_and_highlights_the_cursor_row() -> None:
    items = tuple(ListItem(name, name) for name in ("awx", "jira"))
    component = MultiList("Capabilities", items, frozenset({"awx", "jira"}), cursor=1)
    run = run_solo(component, "enter")
    assert run.model.unhandled == (Activate(),)

    segments = _segments(component)
    highlight = role(DEFAULT, "screen.highlight")
    row = _row(segments, "jira")
    assert style_of(row, "jira").bgcolor == highlight.bgcolor
    assert style_of(row, "jira").color == highlight.color
    assert style_of(row, "✓").color == role(DEFAULT, "screen.success").color
    assert style_of(_row(segments, "awx"), "awx").bgcolor is None


def test_multi_list_uses_the_ascii_symbols_in_plain() -> None:
    items = tuple(ListItem(name, name) for name in ("awx", "jira"))
    frame = run_solo(MultiList("C", items, frozenset({"awx"})), theme=BUILTIN_THEMES["plain"]).frame

    assert "[x] awx" in frame
    assert "[ ] jira" in frame
    assert frame.isascii()


# --- Check ------------------------------------------------------------------------


def test_check_shows_only_a_colored_symbol() -> None:
    on = _segments(Check("Verify TLS", True))
    off = _segments(Check("Verify TLS", False))

    assert style_of(on, "✓").color == role(DEFAULT, "screen.success").color
    assert style_of(off, "✗").color == role(DEFAULT, "screen.error").color
    for run in (run_solo(Check("Verify TLS", True)), run_solo(Check("Verify TLS", False))):
        body = lines(run.frame)[1].strip("│ ")
        assert body in ("✓", "✗")  # no text beside the symbol
        assert not any(word in run.frame.lower() for word in ("true", "false", "enabled", "on "))


def test_check_toggles_on_space_and_enter_and_nothing_else() -> None:
    run = run_solo(Check("Verify TLS"), " ")
    assert field_of(run).value is True
    run = run_solo(Check("Verify TLS", True), "enter")
    assert field_of(run).value is False
    assert run.model.unhandled == ()
    component = Check("Verify TLS")
    assert component.update(Key("x"))[0] is component
    assert component.update(Key("esc"))[0] is component


def test_check_uses_the_plain_symbols_in_the_plain_theme() -> None:
    assert "+" in run_solo(Check("A", True), theme=BUILTIN_THEMES["plain"]).frame
    assert "x" in run_solo(Check("A", False), theme=BUILTIN_THEMES["plain"]).frame


# --- Cycle ------------------------------------------------------------------------


def test_cycle_wraps_and_shows_inherit() -> None:
    component = Cycle("Format", ("table", "json", "yaml"), "yaml", inherited=True)
    assert "· inherit (yaml)" in run_solo(component).frame

    run = run_solo(component, "right")
    assert field_of(run).value == "table"  # wrapped
    assert field_of(run).inherited is False
    assert "\u2039 table \u203a" in run.frame

    run = run_solo(Cycle("Format", ("table", "json", "yaml"), "table"), "left")
    assert field_of(run).value == "yaml"
    run = run_solo(Cycle("Format", ("table", "json"), "other"), "right")
    assert field_of(run).value == "table"
    run = run_solo(Cycle("Format", ("table", "json"), "other"), "left")
    assert field_of(run).value == "json"


def test_cycle_ignores_every_other_key_and_has_nothing_to_cycle_without_choices() -> None:
    component = Cycle("Format", ("a", "b"), "a")
    for key in ("up", "enter", "x", " "):
        assert component.update(Key(key))[0] is component
    empty = Cycle("Format", (), "")
    assert empty.update(Key("right"))[0] is empty


def test_cycle_draws_the_theme_tokens() -> None:
    assert (
        "< json >" in run_solo(Cycle("F", ("json",), "json"), theme=BUILTIN_THEMES["plain"]).frame
    )


# --- shared rules -----------------------------------------------------------------


def _all() -> list[object]:
    items = tuple(ListItem(name, name, detail="d") for name in ("one", "two", "three"))
    return [
        Select("Region", REGIONS, "us-east"),
        Select("Region", REGIONS, "us-east", open=True),
        SingleList("Output", OUTPUTS, "table", cursor=1),
        MultiList("Capabilities", items, frozenset({"one"}), cursor=2),
        Check("Verify TLS", True, help="Check the certificate."),
        Cycle("Format", ("table", "json"), "json"),
        Select("Region", REGIONS, "us-east", open=True).with_error("Pick one."),
    ]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", [(100, 30), (40, 12), (20, 8)])
def test_every_builtin_theme_renders_the_choice_components(
    theme: str, size: tuple[int, int]
) -> None:
    for component in _all():
        for focused in (True, False):
            run = run_solo(component, theme=BUILTIN_THEMES[theme], size=size, focused=focused)
            assert all(len(line) <= size[0] for line in run.frame.splitlines())


def test_plain_choice_frames_are_pure_ascii() -> None:
    for component in _all():
        frame = run_solo(component, theme=BUILTIN_THEMES["plain"]).frame
        assert frame.isascii(), frame


@pytest.mark.parametrize("index", range(7))
def test_an_unrelated_message_returns_the_same_object(index: int) -> None:
    component = _all()[index]
    for message in (Key("ctrl-r"), Key("tab"), Key("ctrl-s"), Activate(), "other"):
        result, cmds = component.update(message)  # type: ignore[attr-defined]
        assert result is component
        assert cmds == []


def test_the_choice_components_are_marked_experimental() -> None:
    for cls in (Check, Cycle, ListItem, MultiList, Select, SingleList):
        assert isinstance(function_mark(cls), Experimental)
