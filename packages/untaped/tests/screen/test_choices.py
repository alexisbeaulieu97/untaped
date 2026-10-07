"""The choice components (Select, SingleList, MultiList, Check, Cycle), Tabs and Buttons."""

from __future__ import annotations

from dataclasses import replace

import pytest

from screen.gallery import field_of, lines, render_styled, role, run_solo, style_of
from untaped.screen.components.buttons import Button, Buttons, Pressed
from untaped.screen.components.choices import Check, Cycle, ListItem, MultiList, Select, SingleList
from untaped.screen.components.inputs import NumberInput, SecretInput, TextInput
from untaped.screen.components.tabs import Tab, Tabs
from untaped.screen.core import Activate, Cancel, Frame, Key, NextField, PrevField
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


def test_check_toggles_on_space_only_and_leaves_enter_to_the_form() -> None:
    run = run_solo(Check("Verify TLS"), " ")
    assert field_of(run).value is True
    run = run_solo(Check("Verify TLS", True), "enter")
    assert field_of(run).value is True  # enter does not toggle
    assert run.model.unhandled == (Activate(),)  # it reaches the form: activate or submit
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


def test_cycle_holds_any_values_and_shows_their_labels() -> None:
    cycle = Cycle("Loud", (None, True, False), None, labels=("unset", "on", "off"))

    assert "unset" in run_solo(cycle).frame
    after = field_of(run_solo(cycle, "right"))
    assert after.value is True
    assert "on" in run_solo(after).frame
    assert field_of(run_solo(cycle, "left")).value is False
    assert field_of(run_solo(cycle, "right", "right", "right")).value is None  # wrapped
    inherited = replace(cycle, value=True, inherited=True)
    assert "inherit (on)" in run_solo(inherited).frame
    unlabelled = Cycle("N", (1, 2), 2)
    assert "2" in run_solo(unlabelled).frame  # without labels a value shows as itself


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


def test_the_choice_components_are_always_valid() -> None:
    for component in _all():
        assert component.validate() == ""  # type: ignore[attr-defined]
    assert _buttons().validate() == ""


def test_every_choice_component_shows_an_error_and_clears_it() -> None:
    for component in _all():
        shown = component.with_error("Nope.")  # type: ignore[attr-defined]
        assert shown.error == "Nope."
        assert shown.with_error("").error == ""
        assert "Nope." in run_solo(shown).frame


def test_the_choice_components_are_marked_experimental() -> None:
    for cls in (Check, Cycle, ListItem, MultiList, Select, SingleList):
        assert isinstance(function_mark(cls), Experimental)


# --- Tabs -------------------------------------------------------------------------


def _token_tabs(**changes: object) -> Tabs:
    base = Tabs(
        "Token source",
        (
            Tab(
                "keychain",
                "Keychain",
                (("token", SecretInput("Token", help="Stored in your keychain, never shown.")),),
            ),
            Tab("enter", "Enter"),
            Tab(
                "command",
                "Command",
                (
                    ("command", TextInput("Command", "op read x", help="Prints the token.")),
                    ("timeout", NumberInput("Timeout", "30", minimum=1, maximum=600)),
                ),
            ),
            Tab("env", "Env", note="Read from the environment."),
        ),
    )
    return replace(base, **changes) if changes else base


def test_tabs_switch_their_own_fields() -> None:
    keychain = run_solo(_token_tabs(), size=(60, 20))
    assert "Token" in keychain.frame
    assert "Stored in your keychain" in keychain.frame
    assert "Command" in keychain.frame  # the tab name only
    assert "Prints the token." not in keychain.frame

    command = run_solo(_token_tabs(), "right", "right")
    assert "op read x" in command.frame
    assert "Prints the token." in command.frame
    assert "Stored in your keychain" not in command.frame
    assert field_of(command).active == "command"

    env = run_solo(_token_tabs(), "right", "right", "right")
    assert "Read from the environment." in env.frame
    assert "op read x" not in env.frame


def test_the_active_tab_is_underlined_with_the_active_token_and_the_others_with_the_inactive() -> (
    None
):
    first = lines(run_solo(_token_tabs()).frame)[2]
    assert first.startswith("│ ━") and first.count("━") > 5 and "─" in first

    plain = lines(run_solo(_token_tabs(), theme=BUILTIN_THEMES["plain"]).frame)[2]
    assert "=" in plain and "-" in plain
    segments = _segments(_token_tabs(), width=46)
    assert style_of(segments, "━").color == role(DEFAULT, "screen.accent").color
    underline = _row(segments, "━")
    assert style_of(underline, "─").color == role(DEFAULT, "screen.border").color


def test_hidden_tabs_keep_what_their_fields_hold() -> None:
    run = run_solo(_token_tabs(), "tab", *"abc", "shift-tab", "right", "left")

    assert field_of(run).active == "keychain"
    assert field_of(run).tabs[0].fields[0][1].value.get_secret_value() == "abc"
    assert field_of(run).value["token"].get_secret_value() == "abc"


def test_tabs_value_is_the_active_tab_and_its_fields_and_validation_covers_only_those() -> None:
    tabs = _token_tabs()
    tabs = replace(
        tabs,
        tabs=(
            replace(
                tabs.tabs[0],
                fields=(
                    (
                        "token",
                        SecretInput("Token", validator=lambda v: "Token needed."),
                    ),
                ),
            ),
            *tabs.tabs[1:],
        ),
    )

    assert tabs.value == {"tab": "keychain", "token": tabs.tabs[0].fields[0][1].value}
    assert tabs.validate() == "Token needed."
    command = replace(tabs, active="command")
    assert command.value == {"tab": "command", "command": "op read x", "timeout": 30}
    assert command.validate() == ""  # the keychain tab's error does not count while hidden
    bad = replace(
        command,
        tabs=tuple(
            replace(
                tab,
                fields=(
                    (
                        "command",
                        TextInput("Command", validator=lambda v: "No."),
                    ),
                ),
            )
            if tab.id == "command"
            else tab
            for tab in command.tabs
        ),
    )
    assert bad.validate() == "No."
    assert _token_tabs(active="env").value == {"tab": "env"}


def test_left_and_right_change_the_tab_only_while_the_header_has_focus() -> None:
    tabs = _token_tabs()
    assert tabs.update(Key("right"))[0].active == "enter"
    assert tabs.update(Key("left"))[0] is tabs  # already on the first tab
    assert _token_tabs(active="env").update(Key("right"))[0].active == "env"

    run = run_solo(_token_tabs(active="command"), "tab", "left", "left", "x")  # in the field
    assert field_of(run).active == "command"
    assert field_of(run).tabs[2].fields[0][1].value == "op readx x"


def test_a_field_in_a_tab_gets_keys_before_the_strip() -> None:
    run = run_solo(_token_tabs(active="command"), "tab", "end", *"!?", "left")

    command = field_of(run).tabs[2].fields[0][1]
    assert command.value == "op read x!?"
    assert command.cursor == len("op read x!")
    assert field_of(run).focus == 1
    assert field_of(run).active == "command"  # left moved the caret, not the tab


def test_tab_walks_the_header_then_each_field_and_hands_focus_to_the_parent_at_the_ends() -> None:
    run = run_solo(_token_tabs(active="command"), "tab")
    assert field_of(run).focus == 1
    run = run_solo(_token_tabs(active="command"), "tab", "tab")
    assert field_of(run).focus == 2
    assert run.model.unhandled == ()
    run = run_solo(_token_tabs(active="command"), "tab", "tab", "tab")
    assert field_of(run).focus == 2  # the last slot: the strip passed on the move
    assert run.model.unhandled == (NextField(),)

    run = run_solo(_token_tabs(active="command", focus=2), "shift-tab", "shift-tab", "shift-tab")
    assert field_of(run).focus == 0
    assert run.model.unhandled == (PrevField(),)

    header = _token_tabs()
    assert header.update(PrevField())[0] is header
    no_fields = _token_tabs(active="enter")
    assert no_fields.update(NextField())[0] is no_fields
    last = _token_tabs(active="command", focus=2)
    assert last.update(NextField())[0] is last


def test_the_focus_ring_follows_the_slot_and_a_field_draws_without_its_own_box() -> None:
    focused = _segments(_token_tabs(active="command", focus=2), width=46)
    unfocused = _segments(_token_tabs(active="command", focus=2), focused=False, width=46)

    assert style_of(focused, "╭").color == role(DEFAULT, "screen.focus").color
    assert style_of(unfocused, "╭").color == role(DEFAULT, "screen.border").color
    frame = run_solo(_token_tabs(active="command", focus=1), width=46).frame
    assert frame.count("╭") == 1  # only the strip's own box
    carets = [s for s in focused if s.style is not None and s.style.reverse]
    assert carets  # the focused field draws its caret


def test_a_tab_edit_clears_the_strips_error_and_a_secret_never_shows() -> None:
    secret = "tok-123-secret"
    run = run_solo(_token_tabs().with_error("Token needed."), "tab", *secret)

    assert "Token needed." not in run.frame  # the edit made the message stale
    assert all(secret not in frame for frame in run.frames)
    assert secret not in repr(field_of(run))
    shown = run_solo(_token_tabs().with_error("Token needed."))
    assert "Token needed." in shown.frame


def test_tabs_need_a_tab_and_fall_back_to_the_first_for_an_unknown_one() -> None:
    with pytest.raises(ValueError, match="at least one tab"):
        Tabs("T", ())
    assert _token_tabs(active="nope").active == "keychain"
    assert _token_tabs(focus=9).focus == 1


# --- Buttons ----------------------------------------------------------------------


def _buttons() -> Buttons:
    return Buttons(
        (
            Button("save", "Save", "primary"),
            Button("anyway", "Save anyway"),
            Button("cancel", "Cancel", "ghost"),
        )
    )


def test_buttons_activate_sends_pressed() -> None:
    run = run_solo(_buttons(), "enter")
    assert run.model.seen == (Pressed("save"),)

    run = run_solo(_buttons(), "right", "enter")
    assert run.model.seen == (Pressed("anyway"),)
    assert field_of(run).value == "anyway"
    run = run_solo(_buttons(), "right", "right", "right", "enter")  # clamped at the last
    assert run.model.seen == (Pressed("cancel"),)


def test_buttons_move_with_left_and_right_and_ignore_everything_else() -> None:
    buttons = _buttons()
    assert buttons.update(Key("left"))[0] is buttons
    assert buttons.update(Key("right"))[0].focus == 1
    for key in ("up", "tab", "x", " ", "esc"):
        assert buttons.update(Key(key))[0] is buttons
    assert buttons.update(Key("enter"))[0] is buttons  # enter is Activate's, after the keys
    assert Buttons(()).update(Activate()) == (Buttons(()), [])
    assert Buttons(()).value == ""


def test_primary_and_secondary_are_boxed_and_ghost_is_plain_text() -> None:
    frame = run_solo(_buttons(), size=(60, 8)).frame

    assert frame.count("╭") == 2
    assert "Cancel" in frame
    assert "│ Cancel" not in frame
    assert lines(frame)[0].count("╭") == 2


def _borders(segments: list, theme: str) -> list:
    """The styles of the top-left corners of the first row of boxes, left to right."""
    corner = Frame(60, 8, BUILTIN_THEMES[theme]).box().top_left  # type: ignore[union-attr]
    first_line = segments[: next(i for i, s in enumerate(segments) if s.text == "\n")]
    return [s.style for s in first_line if corner in s.text and s.style is not None]


@pytest.mark.parametrize("theme", [name for name in THEMES if name != "quiet"])
@pytest.mark.parametrize("focused", [True, False])
def test_a_primary_button_always_has_the_bright_ring_and_a_secondary_the_muted_border(
    theme: str, focused: bool
) -> None:
    for focus in range(3):
        segments = _segments(replace(_buttons(), focus=focus), theme, focused=focused, width=60)
        primary, secondary = _borders(segments, theme)

        assert primary.color == role(BUILTIN_THEMES[theme], "screen.accent").color
        assert secondary.color == role(BUILTIN_THEMES[theme], "screen.border").color
        assert primary != secondary  # the ring is what tells primary from secondary
        assert primary.bold  # bright
        # focus never recolours a border: it is the highlight on the label row
        assert (primary, secondary) == tuple(_borders(_segments(_buttons(), theme), theme))


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("focus", "label"), [(0, "Save"), (1, "Save anyway"), (2, "Cancel")])
def test_the_focused_button_has_the_highlight_fill_and_bright_text_on_its_label_row(
    theme: str, focus: int, label: str
) -> None:
    spec = BUILTIN_THEMES[theme]
    highlight = role(spec, "screen.highlight")
    segments = _segments(replace(_buttons(), focus=focus), theme, width=60)

    style = style_of(segments, label)
    assert style.bgcolor == highlight.bgcolor  # the one allowed fill
    assert style.color == highlight.color  # bright text
    assert bool(style.reverse) == bool(highlight.reverse)
    if focus == 0:
        assert style.bold  # primary stays bold on the highlight
    if spec.border != "none":
        # the fill covers the whole label row, padding included
        cell = next(seg for seg in segments if label in seg.text and seg.style == style)
        assert cell.text == f" {label} "

    unfocused = style_of(_segments(_buttons(), theme, focused=False, width=60), label)
    assert unfocused.bgcolor is None
    assert not unfocused.reverse
    if focus == 0:
        assert unfocused.bold
    if focus == 2:
        assert unfocused.color == role(spec, "screen.muted").color  # a ghost button is muted text


def test_a_theme_that_overrides_the_emphasis_role_restyles_every_emphasised_text() -> None:
    spec = DEFAULT.model_copy(
        update={"color_roles": {**DEFAULT.color_roles, "screen.emphasis": "italic"}}
    )
    frame = Frame(60, 20, spec)

    def styled(component: object, needle: str):
        view = component.view(frame, focused=False, width=46)  # type: ignore[attr-defined]
        return style_of(render_styled(view, width=46), needle)

    for component, needle in (
        (_buttons(), "Save"),
        (Check("Verify", True), BUILTIN_THEMES["default"].symbols["on"]),
        (MultiList("Caps", (ListItem("a", "alpha"),), frozenset({"a"})), "\u2713"),
        (SingleList("Out", OUTPUTS, "table"), "table"),
    ):
        style = styled(component, needle)
        assert style.italic, (type(component).__name__, style)
        assert not style.bold, (type(component).__name__, style)


def test_only_the_focused_button_is_filled() -> None:
    highlight = role(DEFAULT, "screen.highlight")
    for focus, label in ((0, "Save"), (1, "Save anyway"), (2, "Cancel")):
        segments = _segments(replace(_buttons(), focus=focus), width=60)
        filled = [
            s.text for s in segments if s.style is not None and s.style.bgcolor == highlight.bgcolor
        ]
        assert filled == [f" {label} "]


def test_without_a_box_buttons_are_a_line_with_the_chosen_symbol_before_the_focused_one() -> None:
    run = run_solo(_buttons(), "right", theme=BUILTIN_THEMES["quiet"])

    assert lines(run.frame) == ["  Save  ▶ Save anyway    Cancel"]


def test_a_buttons_error_shows_under_the_row() -> None:
    run = run_solo(_buttons().with_error("Check failed."), size=(60, 8))

    assert "Check failed." in run.frame


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", [(100, 30), (40, 12), (20, 8)])
def test_every_builtin_theme_renders_tabs_and_buttons(theme: str, size: tuple[int, int]) -> None:
    for component in (_token_tabs(), _token_tabs(active="command", focus=2), _buttons()):
        for focused in (True, False):
            run = run_solo(component, theme=BUILTIN_THEMES[theme], size=size, focused=focused)
            assert all(len(line) <= size[0] for line in run.frame.splitlines())


def test_plain_tabs_and_buttons_frames_are_pure_ascii() -> None:
    for component in (_token_tabs(), _token_tabs(active="command", focus=2), _buttons()):
        frame = run_solo(component, theme=BUILTIN_THEMES["plain"]).frame
        assert frame.isascii(), frame


def test_tabs_and_buttons_are_marked_experimental() -> None:
    for cls in (Tab, Tabs, Button, Buttons, Pressed):
        assert isinstance(function_mark(cls), Experimental)


# --- review fixes -----------------------------------------------------------------


def _cells(segments: list, needle: str, *, after: int = 0) -> list:
    """The segments inside the box on the line holding ``needle`` (the whole line without a box)."""
    cells = [segment for segment in _row(segments, needle, after=after) if segment.text != "\n"]
    if cells and cells[0].text in ("\u2502", "|"):
        return cells[2:-2]  # the border and its padding
    return cells


def _cursor_row_components() -> list[tuple[str, object, int]]:
    items = tuple(ListItem(name, name, detail="x") for name in ("awx", "jira", "github"))
    return [
        ("multi unchecked", MultiList("C", items, frozenset({"awx"}), cursor=1), 0),
        ("multi checked", MultiList("C", items, frozenset({"jira"}), cursor=1), 0),
        ("single unchosen", SingleList("C", items, "awx", cursor=1), 0),
        ("single chosen", SingleList("C", items, "jira", cursor=1), 0),
        ("select unchosen", Select("C", items, "awx", open=True, cursor=1), 3),
        ("select chosen", Select("C", items, "jira", open=True, cursor=1), 3),
    ]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(
    ("name", "component", "after"), _cursor_row_components(), ids=lambda value: str(value)[:20]
)
def test_the_cursor_row_is_one_highlight_in_every_theme_with_nothing_hidden_in_it(
    theme: str, name: str, component: object, after: int
) -> None:
    highlight = role(BUILTIN_THEMES[theme], "screen.highlight")
    success = role(BUILTIN_THEMES[theme], "screen.success").color
    cells = _cells(_segments(component, theme), "jira", after=after)

    assert cells
    marks = [
        s for s in cells if s.style is not None and s.style.color == success != highlight.color
    ]
    for segment in cells:
        style = segment.style
        assert style is not None
        assert style.bgcolor == highlight.bgcolor, (name, segment)  # the fill reaches every cell
        assert bool(style.reverse) == bool(highlight.reverse), (name, segment)
        assert style.color is None or style.color != style.bgcolor, (name, segment)  # not hidden
        if segment not in marks:
            assert style.color == highlight.color, (name, segment)  # bright text, brackets too
            assert style.bold == highlight.bold, (name, segment)


@pytest.mark.parametrize("theme", ["high-contrast", "classic", "quiet", "plain", "default"])
def test_the_brackets_of_the_cursor_row_are_as_bright_as_its_label(theme: str) -> None:
    items = tuple(ListItem(name, name) for name in ("awx", "jira"))
    cells = _cells(_segments(MultiList("C", items, cursor=1), theme), "jira")

    brackets = [s for s in cells if s.text in ("[", "]")]
    label = next(s for s in cells if "jira" in s.text)
    assert len(brackets) == 2
    assert all(s.style == label.style for s in brackets)


def test_a_select_shows_the_label_of_the_item_and_never_its_id() -> None:
    items = (ListItem("us-1", "US East"), ListItem("eu-2", "EU Central"))
    select = Select("Region", items, "eu-2")

    assert select.shown == "EU Central"
    closed = run_solo(select).frame
    assert "EU Central" in closed
    assert "eu-2" not in closed
    opened = run_solo(select, "enter").frame
    assert "US East" in opened
    assert "us-1" not in opened
    assert "eu-2" not in opened
    assert field_of(run_solo(select, "enter", "up", "enter")).value == "us-1"  # the value is the id
    assert Select("Region", items, "unlisted").shown == "unlisted"


def test_a_single_list_starts_with_its_cursor_on_the_value() -> None:
    assert SingleList("O", OUTPUTS, "yaml").cursor == 2
    assert SingleList("O", OUTPUTS, "json").cursor == 1
    assert SingleList("O", OUTPUTS, "unlisted").cursor == 0
    assert SingleList("O", OUTPUTS, "yaml", cursor=0).cursor == 0  # an explicit cursor wins
    highlight = role(DEFAULT, "screen.highlight")
    segments = _segments(SingleList("O", OUTPUTS, "yaml"))
    assert style_of(_row(segments, "yaml"), "yaml").bgcolor == highlight.bgcolor
    assert style_of(_row(segments, "table"), "table").bgcolor is None


@pytest.mark.parametrize(
    ("height", "rows"), [(4, 3), (8, 3), (9, 3), (10, 4), (12, 6), (14, 8), (40, 8)]
)
def test_a_list_shows_as_many_rows_as_the_frame_has_room_for(height: int, rows: int) -> None:
    items = tuple(ListItem(f"row{n:02d}", f"row{n:02d}") for n in range(30))
    frame = Frame(60, height, DEFAULT)

    for component in (SingleList("L", items, cursor=15), MultiList("L", items, cursor=15)):
        rendered = render_styled(component.view(frame, focused=True, width=30), width=30)
        text = "".join(segment.text for segment in rendered)
        assert text.count("row") == rows, (height, text)
        assert "row15" in text  # the cursor row is always one of them


def _focus_marks(segments: list) -> tuple[list, list]:
    """The caret segments and the accent-coloured label segments of a rendered strip."""
    carets = [s for s in segments if s.style is not None and s.style.reverse]
    accent = role(DEFAULT, "screen.accent").color
    return carets, [s for s in segments if s.style is not None and s.style.color == accent]


def _two_field_tabs(**changes: object) -> Tabs:
    return Tabs(
        "Strip",
        (
            Tab(
                "one",
                "One",
                (("first", TextInput("First", "aa")), ("second", TextInput("Second", "bb"))),
            ),
            Tab("two", "Two"),
        ),
        **changes,  # type: ignore[arg-type]
    )


def test_tabs_pass_focus_only_to_the_focused_slot() -> None:
    for slot, caret_on in ((1, "a"), (2, "b")):
        carets, accent = _focus_marks(_segments(_two_field_tabs(focus=slot), width=40))

        assert [c.text for c in carets] == [" "]  # one caret, past the end of one value
        labels = [s.text.strip() for s in accent]
        focused_label = "First" if caret_on == "a" else "Second"
        other_label = "Second" if caret_on == "a" else "First"
        assert focused_label in labels
        assert other_label not in labels  # the other field's label stays muted
        assert "Strip" in labels  # the strip's own title is emphasised with its focus
    carets, accent = _focus_marks(_segments(_two_field_tabs(focus=0), width=40))
    assert carets == []  # the header has focus: no field draws a caret
    assert "One" in [s.text.strip() for s in accent]  # the active tab name, accent while focused
    carets, accent = _focus_marks(_segments(_two_field_tabs(focus=2), focused=False, width=40))
    assert carets == []
    assert not {"First", "Second", "Strip"} & {s.text.strip() for s in accent}


def test_the_active_tab_is_accented_only_while_the_header_has_focus() -> None:
    value = role(DEFAULT, "screen.value").color
    accent = role(DEFAULT, "screen.accent").color
    header = _segments(_two_field_tabs(focus=0), width=40)
    fields = _segments(_two_field_tabs(focus=1), width=40)

    assert style_of(header, "One").color == accent
    assert style_of(header, "One").bold
    assert style_of(fields, "One").color == value
    assert style_of(fields, "One").bold
    assert style_of(header, "Two").color == role(DEFAULT, "screen.muted").color
