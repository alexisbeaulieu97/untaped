"""The picker as a screen: today's end-to-end scenarios and what the user sees.

The scenarios are the ones ``test_app.py`` ran against the real prompt_toolkit
application, now through ``drive_screen``; the adapter cases it also held
(unbound escape sequences, an external SIGINT, closed input) live in
``tests/screen/test_terminal.py``. The reducer has its own tests in
``test_state.py``.
"""

from __future__ import annotations

import pytest
from rich.cells import cell_len

from screen.gallery import render_styled, role
from untaped.picker import (
    GENERIC_ALTERNATIVE,
    GENERIC_COMMAND,
    PickCatalog,
    PickItem,
    PickRequest,
    PickResult,
    PickSetting,
)
from untaped.picker.screen import _SearchPane, _SelectedPane, picker_screen
from untaped.picker.state import PickerState, handle, initial_state, visible
from untaped.screen.core import Back, Cancel, Frame, Interrupt, Key, Paste, Quit
from untaped.testing import ScreenRun, drive_screen
from untaped.testing.screens import rendered_text
from untaped.theme import BUILTIN_THEMES

DEFAULT = BUILTIN_THEMES["default"]
LEFT, RIGHT = DEFAULT.symbols["cycle.left"], DEFAULT.symbols["cycle.right"]
ITEMS = (
    PickItem(id="acme/api", label="acme/api"),
    PickItem(id="acme/web", label="acme/web"),
)
SETTINGS = (PickSetting(key="mode", label="mode", default="write", choices=("write", "read-only")),)
MANY = tuple(PickItem(id=f"acme/r{i:02}", label=f"acme/r{i:02}") for i in range(30))
MANY_SETTINGS = (
    PickSetting(key="mode", label="mode", default="write", choices=("write", "read-only")),
    PickSetting(key="base", label="base", placeholder="default"),
)


def _request(**extra: object) -> PickRequest:
    return PickRequest(
        heading="New workspace", catalog=PickCatalog(ITEMS), settings=SETTINGS, **extra
    )  # type: ignore[arg-type]


def _many(**extra: object) -> PickRequest:
    return PickRequest(
        heading="New workspace",
        catalog=PickCatalog(MANY, note="refreshed 2h ago"),
        settings=MANY_SETTINGS,
        **extra,
    )  # type: ignore[arg-type]


def _run(
    request: PickRequest, *keys: object, **options: object
) -> ScreenRun[PickerState, PickResult]:
    return drive_screen(picker_screen(request), keys, **options)  # type: ignore[arg-type]


def _picked_ids(run: ScreenRun[PickerState, PickResult]) -> list[str]:
    assert run.result is not None, run.frame
    return [pick.item.id for pick in run.result.picks]


def _lines(frame: str) -> list[str]:
    return frame.split("\n")


# --- today's scenarios -------------------------------------------------------------


def test_type_select_and_confirm() -> None:
    run = _run(_request(), "w", "e", "b", "down", " ", "ctrl-s")
    assert _picked_ids(run) == ["acme/web"]


def test_settings_change_in_the_selected_pane() -> None:
    run = _run(_request(), "w", "e", "b", "down", " ", "tab", "down", "right", "ctrl-s")
    assert run.result is not None
    assert run.result.defaults == {"mode": "read-only"}


def test_ctrl_c_with_nothing_selected_cancels() -> None:
    run = _run(_request(), "ctrl-c")
    assert run.outcome == Cancel()
    assert run.result is None


def test_ctrl_c_then_y_discards_a_selection() -> None:
    run = _run(_request(), "down", " ", "ctrl-c", "y")
    assert run.outcome == Cancel()


def test_ctrl_c_then_anything_else_keeps_the_selection() -> None:
    run = _run(_request(), "down", " ", "ctrl-c", "n")
    assert run.outcome is None
    assert "discard" not in run.frame
    assert "[✓] acme/api" in run.frame


def test_title_field_is_typed_first() -> None:
    run = _run(_request(title_label="name"), *"JIRA-1", "enter", "down", " ", "ctrl-s")
    assert run.result is not None
    assert run.result.title == "JIRA-1"


def test_refresh_runs_at_start_and_on_ctrl_r() -> None:
    calls: list[bool] = []

    def refresh(force: bool) -> PickCatalog:
        calls.append(force)
        return PickCatalog((*ITEMS, PickItem(id="acme/new", label="acme/new")), note="just now")

    run = _run(_request(refresh=refresh), "ctrl-r", *"new", "down", " ", "ctrl-s")
    assert calls == [False, True]
    assert _picked_ids(run) == ["acme/new"]
    assert run.commands_run == ("refresh", "refresh")


def test_the_first_refresh_shows_while_it_runs_and_clears_when_it_lands() -> None:
    state = picker_screen(_request(refresh=lambda _force: PickCatalog(ITEMS))).init()[0]
    assert state.refreshing

    run = _run(_request(refresh=lambda _force: PickCatalog(ITEMS, note="just now")))
    assert not run.model.refreshing
    assert "just now" in run.frame
    assert "refreshing" not in run.frame


def test_refresh_errors_keep_the_picker_usable() -> None:
    def refresh(force: bool) -> PickCatalog:
        raise RuntimeError("HTTP 503")

    run = _run(_request(refresh=refresh), *"api", "down", " ")
    assert "refresh failed" in run.frames[0]
    run = _run(_request(refresh=refresh), *"api", "down", " ", "ctrl-s")
    assert _picked_ids(run) == ["acme/api"]


def test_a_refresh_error_with_no_message_names_the_exception() -> None:
    def refresh(force: bool) -> PickCatalog:
        raise TimeoutError

    run = _run(_request(refresh=refresh))
    assert "refresh failed: TimeoutError" in run.frame


def test_ctrl_r_retries_after_a_failed_refresh_even_with_the_error_showing() -> None:
    calls: list[bool] = []

    def refresh(force: bool) -> PickCatalog:
        calls.append(force)
        if len(calls) == 1:
            raise RuntimeError("HTTP 503")
        return PickCatalog(ITEMS, note="just now")

    run = _run(_request(refresh=refresh), "ctrl-r")
    assert calls == [False, True]
    assert "refresh failed" not in run.frame
    assert "just now" in run.frame


def test_pasted_text_lands_in_the_search() -> None:
    run = _run(_request(), Paste("web"), "down", " ", "ctrl-s")
    assert _picked_ids(run) == ["acme/web"]


def test_a_paste_after_the_outcome_is_decided_acts_once() -> None:
    run = _run(_request(), "down", " ", "ctrl-c", Paste("yy"))
    assert run.outcome == Cancel()


def test_a_paste_drops_control_characters() -> None:
    run = _run(_request(), Paste("w\x07e\nb"))
    assert run.model.query == "web"


def test_ctrl_r_while_a_refresh_is_running_does_not_start_another() -> None:
    screen = picker_screen(_request(refresh=lambda _force: PickCatalog(ITEMS)))
    state, cmds = screen.init()
    assert state.refreshing
    assert [cmd.name for cmd in cmds] == ["refresh"]
    binding = next(b for b in screen.keys if b.key == "ctrl-r")

    again, more = screen.update(state, binding.message)

    assert again is state
    assert more == []


def test_esc_in_the_selected_pane_asks_before_discarding() -> None:
    asked = _run(_request(), "down", " ", "tab", "esc")
    assert "discard 1 selected? y/n" in asked.frame
    assert asked.outcome is None

    declined = _run(_request(), "down", " ", "tab", "esc", "n")
    assert declined.outcome is None
    assert "discard" not in declined.frame

    discarded = _run(_request(), "down", " ", "tab", "esc", "y")
    assert discarded.outcome == Cancel()


def test_esc_with_nothing_selected_in_the_selected_pane_cancels() -> None:
    assert _run(_request(), "tab", "esc").outcome == Cancel()


def test_esc_in_the_search_clears_the_query_and_never_quits() -> None:
    run = _run(_request(), *"web", "esc", "esc")
    assert run.model.query == ""
    assert run.outcome is None


def test_esc_goes_back_even_while_an_error_is_showing() -> None:
    run = _run(_request(title_label="name"), "ctrl-s")
    assert "name is required" in run.frame

    assert _run(_request(title_label="name"), "ctrl-s", "esc").outcome == Cancel()


def test_the_error_stays_until_a_key_that_does_something() -> None:
    run = _run(_request(), "ctrl-s", "home")
    assert "select at least one item" in run.frame
    assert "select at least one item" not in _run(_request(), "ctrl-s", "a").frame


def test_back_and_interrupt_ask_like_ctrl_c() -> None:
    screen = picker_screen(_request())
    selected = handle(handle(initial_state(_request()), "down"), " ")
    for message in (Back(), Interrupt()):
        asked, cmds = screen.update(selected, message)
        assert asked.quitting
        assert cmds == []
    cancelled, cmds = screen.update(initial_state(_request()), Back())
    assert cancelled.outcome == "cancelled"
    assert [cmd.fn() for cmd in cmds] == [Cancel()]


def test_other_messages_are_ignored() -> None:
    screen = picker_screen(_request())
    state = screen.init()[0]
    assert screen.update(state, object()) == (state, [])


def test_confirming_quits_with_the_resolved_result() -> None:
    run = _run(_request(), "down", " ", "ctrl-s")
    assert isinstance(run.outcome, Quit)
    assert run.result is not None
    assert [pick.settings for pick in run.result.picks] == [{"mode": "write"}]


def test_refresh_is_not_offered_without_a_source() -> None:
    assert "refresh" not in _run(_request(), "down", " ").frame
    assert "ctrl-r refresh" in _run(_request(refresh=lambda _force: PickCatalog(ITEMS))).frame


# --- the screen's declarations -----------------------------------------------------


def test_the_screen_is_full_screen_and_titled_by_the_heading() -> None:
    screen = picker_screen(_request())
    assert screen.layout == "full"
    assert screen.title == "New workspace"


def test_without_a_command_the_refusal_is_generic() -> None:
    screen = picker_screen(_request())
    assert (screen.command, screen.alternative) == (GENERIC_COMMAND, GENERIC_ALTERNATIVE)


def test_a_request_names_its_command_and_alternative() -> None:
    screen = picker_screen(_request(command="untaped workspace create", alternative="--repo"))
    assert (screen.command, screen.alternative) == ("untaped workspace create", "--repo")


# --- what the user sees ------------------------------------------------------------


def test_header_shows_the_heading_title_and_subtitle() -> None:
    run = _run(_many(title="JIRA-1", subtitle=lambda title, _defaults: f"feature/{title}"))
    header = _lines(run.frame)[0]
    assert "◆ New workspace" in header
    assert "JIRA-1" in header
    assert header.rstrip().endswith("feature/JIRA-1")


def test_the_title_field_shows_its_label_until_something_is_typed() -> None:
    run = _run(_many(title_label="name"))
    assert "name" in _lines(run.frame)[0]
    assert "name" not in _lines(_run(_many(title_label="name"), "J").frame)[0]


def test_wide_terminals_put_the_panes_side_by_side() -> None:
    lines = _lines(_run(_many(), size=(120, 30)).frame)
    assert any("Search" in line and "Selected 0" in line for line in lines)


def test_narrow_terminals_stack_the_panes() -> None:
    lines = _lines(_run(_many(), size=(80, 30)).frame)
    search = next(i for i, line in enumerate(lines) if "Search" in line)
    selected = next(i for i, line in enumerate(lines) if "Selected 0" in line)
    assert selected > search
    assert "Selected" not in lines[search]


def test_every_line_fits_the_width() -> None:
    selecting = ("down", " ", "tab", "down")
    for width in (60, 99, 100, 140):
        for keys in ((), selecting):
            for line in _lines(_run(_many(), *keys, size=(width, 30)).frame):
                assert cell_len(line) <= width, (width, line)


def test_the_panes_and_the_footer_fit_the_terminal_height() -> None:
    selecting = ("down", " ", "tab")
    for size in ((80, 24), (100, 24), (120, 30)):
        for keys in ((), selecting, (*selecting, "down")):
            frame = _run(_many(), *keys, size=size).frame
            assert len(_lines(frame)) <= size[1], (size, keys)


def test_a_short_terminal_keeps_create_and_the_footer_visible() -> None:
    selecting = ("down", " ", "tab")
    for keys in ((), selecting):
        lines = _lines(_run(_many(), *keys, size=(80, 24)).frame)
        assert any("Create" in line for line in lines), keys
        assert "esc back" in lines[-1], keys


def test_a_very_short_terminal_still_draws_something() -> None:
    for height in (4, 8, 12):
        frame = _run(_many(), "down", " ", "tab", size=(80, height)).frame
        assert "esc back" in frame


def test_the_list_is_windowed_around_the_cursor() -> None:
    run = _run(_many(), "down", *["down"] * 14)
    assert "acme/r14" in run.frame
    assert "acme/r00" not in run.frame


def test_selected_items_are_marked_and_listed_on_the_right() -> None:
    frame = _run(_many(), "down", " ").frame
    assert "[✓] acme/r00" in frame
    assert "Selected 1" in frame
    assert "▸ acme/r00" in frame
    assert "2 selected" not in frame
    assert "30 · 1 selected · refreshed 2h ago" in frame


def test_the_all_items_row_opens_to_its_settings() -> None:
    frame = _run(_many(), "down", " ").frame
    assert "▾ all items" in frame
    assert "mode" in frame
    assert f"{LEFT} write {RIGHT}" in frame
    assert "base" in frame
    assert "default" in frame


def test_moving_to_an_item_opens_it_and_inherited_values_say_so() -> None:
    frame = _run(_many(), "down", " ", "tab", "down", "down", "down", "down").frame
    assert "▾ acme/r00" in frame
    assert "· inherit (write)" in frame
    assert "· inherit (default)" in frame


def test_changing_an_items_value_replaces_the_inherit_note() -> None:
    frame = _run(_many(), "down", " ", "tab", "down", "down", "down", "down", "right").frame
    assert f"{LEFT} read-only {RIGHT}" in frame


def test_an_empty_value_without_a_placeholder_shows_the_dash() -> None:
    request = PickRequest(
        heading="New",
        catalog=PickCatalog(ITEMS),
        settings=(PickSetting(key="base", label="base"),),
    )
    assert "—" in _run(request).frame
    assert "—" not in _run(request, size=(100, 30), theme=BUILTIN_THEMES["plain"]).frame


def test_the_count_line_shows_the_note_and_the_refresh_state() -> None:
    assert "30 · refreshed 2h ago" in _run(_many()).frame


def test_the_count_line_says_while_a_refresh_runs() -> None:
    screen = picker_screen(_many(refresh=lambda _force: PickCatalog(MANY)))
    state, _cmds = screen.init()  # the catalog has not landed yet
    frame = rendered_text(screen.view(state, Frame(100, 30, DEFAULT)), 100, 30)
    assert "30 · refreshed 2h ago · refreshing…" in frame


def test_the_panes_ask_nothing_of_the_drawn_state() -> None:
    state = initial_state(_many())
    for pane in (_SearchPane(state), _SelectedPane(state)):
        assert pane.update(Key("a")) == (pane, [])
        assert pane.with_error("no") is pane
        assert pane.validate() == ""
        assert (pane.value, pane.error) == ("", "")


def test_a_failed_refresh_stays_marked_until_one_succeeds() -> None:
    calls = 0

    def refresh(force: bool) -> PickCatalog:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("HTTP 503")
        return PickCatalog(MANY, note="just now")

    failed = _run(_many(refresh=refresh), "down")
    assert "30 · refreshed 2h ago · refresh failed" in failed.frame

    recovered = _run(_many(refresh=refresh), "down", "ctrl-r")
    assert "refresh failed" not in recovered.frame
    assert "30 · just now" in recovered.frame


def test_errors_show_under_the_panes() -> None:
    lines = _lines(_run(_many(), "ctrl-s").frame)
    at = next(i for i, line in enumerate(lines) if "select at least one item" in line)
    assert lines[at - 1].startswith("╰")


def test_the_discard_question_shows_under_the_panes() -> None:
    frame = _run(_many(), "down", " ", "ctrl-c").frame
    assert "discard 1 selected? y/n" in frame


def test_the_footer_lists_the_keys_that_apply() -> None:
    searching = _lines(_run(_many()).frame)[-1]
    assert "down browse" in searching

    browsing = _lines(_run(_many(), "down").frame)[-1]
    assert "space toggle" in browsing
    assert "/ search" in browsing

    on_item = _lines(_run(_many(), "down", " ", "tab", "down", "down", "down").frame)[-1]
    assert "space remove" in on_item
    on_choice = _lines(_run(_many(), "down", " ", "tab", "down").frame)[-1]
    assert "left previous" in on_choice
    assert "right next" in on_choice


def test_the_help_overlay_lists_the_pickers_keys_and_the_shared_ones() -> None:
    frame = _run(_many(), "down", "tab", "?").frame
    assert "Keys" in frame
    for entry in ("ctrl-s", "submit", "tab", "ctrl-c"):
        assert entry in frame


def test_a_question_mark_in_the_search_is_text() -> None:
    run = _run(_many(), "?")
    assert run.model.query == "?"
    assert "Keys" not in run.frame


def test_wide_characters_are_measured_in_terminal_cells() -> None:
    wide = PickItem(id="wide", label="界" * 40, description="説明")
    request = PickRequest(
        heading="New workspace", catalog=PickCatalog((wide, *ITEMS)), settings=SETTINGS
    )
    for width in (100, 80):
        lines = _lines(_run(request, "down", " ", "tab", size=(width, 30)).frame)
        assert all(cell_len(line) <= width for line in lines), width
        boxes = [line for line in lines if line.startswith(("╭", "│", "╰"))]
        assert boxes
        assert all(cell_len(line) == width for line in boxes), width


def test_narrow_terminals_render_at_their_real_width() -> None:
    for width in range(12, 41):
        frame = _run(_many(), "down", " ", size=(width, 40)).frame
        lines = _lines(frame)
        assert all(cell_len(line) <= width for line in lines), width
        assert any("Create" in line for line in lines), width
        assert not any("Creat…" in line for line in lines), width


def test_below_ten_inner_columns_the_create_button_loses_its_box() -> None:
    wide = _lines(_run(_many(), "tab", size=(30, 40)).frame)
    narrow = _lines(_run(_many(), "tab", size=(13, 40)).frame)
    assert any("Create" in line and "│ Create │" in line for line in wide)
    assert not any("│ Create │" in line for line in narrow)
    assert any("Create" in line for line in narrow)


def test_the_list_ranks_like_the_reducer_does() -> None:
    def adhoc(query: str) -> PickItem | None:
        return PickItem(id=f"git@h:{query}", label=f"git@h:{query}") if "/" in query else None

    items = (
        PickItem(id="acme/web", label="acme/web", description="Frontend"),
        PickItem(id="acme/api", label="acme/api"),
        PickItem(id="old/web", label="old/web", dimmed=True),
    )
    request = PickRequest(heading="New", catalog=PickCatalog(items), adhoc=adhoc)
    for query in ("", "web", "acme/we", "front"):
        run = _run(request, *query)
        shown = [
            line.split("]", 1)[1].split("│")[0].strip()
            for line in _lines(run.frame)
            if line.startswith("│ [")
        ]
        expected = [entry.item.label for entry in visible(run.model)]
        assert [label.split()[0] for label in shown] == [label.split()[0] for label in expected]


def test_a_long_list_of_selections_keeps_the_tree_window_and_the_button() -> None:
    keys = ["down", " "] * 25  # twelve rows of selections would overflow a pane
    run = _run(_many(), *keys, "tab", size=(120, 30))
    assert any("Create" in line for line in _lines(run.frame))
    assert len(_lines(run.frame)) <= 30


# --- editing a text setting --------------------------------------------------------


def _text_request(**extra: object) -> PickRequest:
    return PickRequest(
        heading="New",
        catalog=PickCatalog(ITEMS),
        settings=(
            PickSetting(
                key="base",
                label="base",
                default="main",
                complete=lambda _item: ("main", "develop", "devops"),
            ),
        ),
        **extra,
    )  # type: ignore[arg-type]


def test_enter_edits_a_text_setting_in_a_field_with_candidates() -> None:
    run = _run(_text_request(), "down", " ", "tab", "down", "enter", "ctrl-u", "d", "e")
    frame = run.frame
    assert run.model.editing == "de"
    assert "base" in frame
    assert "de" in frame
    assert "develop" in frame
    assert "devops" in frame
    assert "enter save · esc cancel" in frame


def test_tab_completes_the_first_candidate_and_enter_saves_it() -> None:
    run = _run(_text_request(), "down", " ", "tab", "down", "enter", "ctrl-u", "d", "tab", "enter")
    assert run.model.editing is None
    assert run.model.defaults == {"base": "develop"}
    assert "develop" in run.frame


def test_esc_abandons_an_edit_and_keeps_the_screen() -> None:
    run = _run(_text_request(), "down", " ", "tab", "down", "enter", "x", "esc")
    assert run.model.editing is None
    assert run.outcome is None
    assert run.model.defaults == {"base": "main"}


def test_the_editor_leaves_the_panes_a_button_and_a_footer() -> None:
    frame = _run(_text_request(), "down", " ", "tab", "down", "enter", "d", size=(80, 24)).frame
    assert "Create" in frame
    assert "esc back" in _lines(frame)[-1]


# --- the look, from the theme ------------------------------------------------------


def _segments(*keys: str, theme: str = "default", request: PickRequest | None = None):
    request = request or _many()
    state = _run(request, *keys, theme=BUILTIN_THEMES[theme]).model
    screen = picker_screen(request)
    return render_styled(screen.view(state, Frame(100, 30, BUILTIN_THEMES[theme])), width=100)


def _fill(segments, needle):
    return next(s for s in segments if needle in s.text).style


def test_searching_draws_the_caret_and_no_cursor_row() -> None:
    segments = _segments()
    fill = role(DEFAULT, "screen.highlight").bgcolor
    assert not any(s.style is not None and s.style.bgcolor == fill for s in segments)
    assert any(s.style is not None and s.style.reverse for s in segments)


def test_browsing_draws_the_cursor_row_and_no_caret() -> None:
    segments = _segments("down")
    fill = role(DEFAULT, "screen.highlight").bgcolor
    row = next(s for s in segments if "acme/r00" in s.text)
    assert row.style is not None
    assert row.style.bgcolor == fill
    assert not any(s.style is not None and s.style.reverse for s in segments)


def test_the_focused_pane_has_the_focus_border() -> None:
    focus = role(DEFAULT, "screen.focus").color
    border = role(DEFAULT, "screen.border").color

    def top_borders(*keys: str) -> list[object]:
        tops = [s for s in _segments(*keys) if s.text.startswith("╭")]
        return [s.style.color for s in tops[:2]]  # type: ignore[union-attr]

    assert top_borders() == [focus, border]
    assert top_borders("tab") == [border, focus]


def test_the_cursor_row_of_the_right_pane_shows_only_while_it_has_focus() -> None:
    fill = role(DEFAULT, "screen.highlight").bgcolor

    def filled(*keys: str) -> bool:
        return any(
            s.style is not None and s.style.bgcolor == fill and "all items" in s.text
            for s in _segments(*keys)
        )

    assert not filled("down", " ")
    assert filled("down", " ", "tab")


@pytest.mark.parametrize("name", sorted(BUILTIN_THEMES))
def test_every_builtin_theme_draws_the_picker(name: str) -> None:
    theme = BUILTIN_THEMES[name]
    for size in ((120, 30), (80, 24), (40, 20)):
        for keys in (
            (),
            ("down", " ", "tab", "down", "down"),
            ("ctrl-s",),
            ("down", " ", "ctrl-c"),
        ):
            frame = _run(_many(subtitle=lambda t, _d: t), *keys, size=size, theme=theme).frame
            assert all(cell_len(line) <= size[0] for line in _lines(frame)), (name, size, keys)


def test_plain_picker_frames_are_pure_ascii() -> None:
    plain = BUILTIN_THEMES["plain"]
    scenes = (
        (),
        ("down", " ", "tab", "down", "down"),
        ("ctrl-s",),
        ("down", " ", "ctrl-c"),
        ("?",),
    )
    for size in ((120, 30), (80, 24)):
        for keys in scenes:
            frame = _run(_many(title_label="name"), *keys, size=size, theme=plain).frame
            assert frame.isascii(), (size, keys, [c for c in frame if not c.isascii()])


def test_without_a_border_the_panes_have_titles_and_no_box() -> None:
    borderless = BUILTIN_THEMES["default"].model_copy(update={"border": "none"})
    frame = _run(_many(), "down", " ", size=(120, 30), theme=borderless).frame
    assert "Search" in frame
    assert "Selected 1" in frame
    assert not any(char in frame for char in "╭╮╰╯│─")


def test_ascii_borders_draw_the_panes() -> None:
    frame = _run(_many(), size=(120, 30), theme=BUILTIN_THEMES["plain"]).frame
    assert "+-" in frame


def test_only_the_state_is_the_models_key_press() -> None:
    # The screen's model is the reducer's state: the same keys give the same state.
    run = _run(_request(), "w", "e", "b", "down", " ")
    state = initial_state(_request())
    for key in ("w", "e", "b", "down", " "):
        state = handle(state, key)
    assert run.model == state
    assert Key("w") == Key("w")
