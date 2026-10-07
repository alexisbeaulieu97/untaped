"""``SearchList``: filtering, match highlighting, dimmed rows, windowing and its keys."""

from __future__ import annotations

import pytest
from rich.segment import Segment

from screen.gallery import field_of, lines, render_styled, role, run_solo, style_of
from untaped.screen.components import lists
from untaped.screen.components.choices import ListItem
from untaped.screen.components.lists import SearchList
from untaped.screen.core import Cancel, Frame, Key, Paste
from untaped.stability import Experimental, function_mark
from untaped.theme import BUILTIN_THEMES

THEMES = sorted(BUILTIN_THEMES)
DEFAULT = BUILTIN_THEMES["default"]
REPOS = tuple(
    ListItem(name, name, detail=detail, description=desc, dimmed=name in ("legacy/api",))
    for name, detail, desc in (
        ("acme/api", "py", "Core REST API"),
        ("acme/api-gateway", "go", "Edge"),
        ("acme/web", "ts", "Frontend"),
        ("legacy/api", "", "Old"),
        ("acme/docs", "md", ""),
    )
)
MANY = tuple(ListItem(f"item-{n:04d}", f"item {n:04d}") for n in range(2000))


def _view(component: SearchList, *, focused: bool = True, height: int = 20, width: int = 46):
    return render_styled(
        component.view(Frame(60, height, DEFAULT), focused=focused, width=width), width=width
    )


def _matched(segments: list[Segment]) -> list[str]:
    """The text of every bold-and-underlined segment: what a search matched."""
    return [
        segment.text
        for segment in segments
        if segment.style is not None and segment.style.bold and segment.style.underline
    ]


def _labels(run) -> list[str]:  # type: ignore[no-untyped-def]
    return [matched.item.label for matched in field_of(run).matches]


# --- filtering ---------------------------------------------------------------------


def test_every_item_shows_before_a_query_with_the_count() -> None:
    run = run_solo(SearchList("Repos", REPOS))

    assert _labels(run) == [item.label for item in REPOS if not item.dimmed] + ["legacy/api"]
    assert "acme/web" in run.frame
    assert lines(run.frame)[-2].rstrip(" │").endswith("5")  # the count line


def test_typing_filters_without_a_prefix_key_and_shows_the_counts() -> None:
    run = run_solo(SearchList("Repos", REPOS), *"api")

    assert field_of(run).query == "api"
    assert _labels(run) == ["acme/api", "acme/api-gateway", "legacy/api"]
    assert "acme/web" not in run.frame
    assert "3 of 5" in run.frame
    assert "/" in run.frame  # a slash is just text


def test_a_slash_types_into_the_query() -> None:
    run = run_solo(SearchList("Repos", REPOS), *"acme/w")

    assert field_of(run).query == "acme/w"
    assert _labels(run)[0] == "acme/web"  # the substring beats the subsequence matches


def test_more_than_one_word_must_all_match() -> None:
    run = run_solo(SearchList("Repos", REPOS), *"acme rest")

    assert field_of(run).query == "acme rest"  # a space inside a query is text
    assert _labels(run) == ["acme/api"]  # "rest" is in its description


def test_no_match_says_so_and_keeps_the_cursor_safe() -> None:
    run = run_solo(SearchList("Repos", REPOS), *"zzz", "enter", "down")

    assert "no matches" in run.frame
    assert field_of(run).selected == frozenset()
    assert field_of(run).cursor == 0


def test_dimmed_items_sort_last_and_are_muted() -> None:
    run = run_solo(SearchList("Repos", REPOS), *"api")
    segments = _view(SearchList("Repos", REPOS, query="api"))

    assert _labels(run)[-1] == "legacy/api"
    assert style_of(segments, "legacy/").dim
    assert not style_of(segments, "acme/").dim


def test_backspace_ctrl_u_and_ctrl_w_edit_the_query() -> None:
    assert field_of(run_solo(SearchList("R", REPOS), *"api", "backspace")).query == "ap"
    assert field_of(run_solo(SearchList("R", REPOS), *"api", "ctrl-u")).query == ""
    assert field_of(run_solo(SearchList("R", REPOS), *"acme web", "ctrl-w")).query == "acme "


def test_a_paste_lands_in_the_query() -> None:
    run = run_solo(SearchList("Repos", REPOS), Paste("acme/web\n"))

    assert field_of(run).query == "acme/web"
    assert _labels(run) == ["acme/web"]


def test_changing_the_query_puts_the_cursor_back_on_the_first_row() -> None:
    run = run_solo(SearchList("Repos", REPOS), "down", "down", "a")

    assert field_of(run).cursor == 0


# --- highlighting ------------------------------------------------------------------


def test_the_matched_letters_are_bold_and_underlined() -> None:
    marked = _matched(_view(SearchList("Repos", REPOS, query="web")))

    assert "".join(marked) == "web"


def test_a_subsequence_match_highlights_each_letter_it_used() -> None:
    marked = _matched(_view(SearchList("Repos", REPOS, query="agw")))

    assert "".join(marked) == "agw"
    assert all(len(part) == 1 or part.isalpha() for part in marked)


def test_the_cursor_row_keeps_its_highlight_under_the_matches() -> None:
    segments = _view(SearchList("Repos", REPOS, query="web"))
    cursor_fill = role(DEFAULT, "screen.highlight").bgcolor

    web = next(s for s in segments if s.text == "web" and s.style is not None and s.style.underline)
    assert web.style is not None
    assert web.style.bgcolor == cursor_fill
    assert web.style.bold


def test_an_unfocused_list_shows_no_cursor_row_and_no_caret() -> None:
    segments = _view(SearchList("Repos", REPOS, query="web"), focused=False)

    assert not any(s.style is not None and s.style.reverse for s in segments)
    fill = role(DEFAULT, "screen.highlight").bgcolor
    assert not any(s.style is not None and s.style.bgcolor == fill for s in segments)


def test_caret_and_highlight_can_be_drawn_one_at_a_time() -> None:
    fill = role(DEFAULT, "screen.highlight").bgcolor

    def parts(**flags: bool) -> tuple[bool, bool]:
        segments = _view(SearchList("Repos", REPOS, query="web", **flags))
        return (
            any(s.style is not None and s.style.reverse for s in segments),
            any(s.style is not None and s.style.bgcolor == fill for s in segments),
        )

    assert parts() == (True, True)
    assert parts(highlight=False) == (True, False)
    assert parts(caret=False) == (False, True)


# --- keys --------------------------------------------------------------------------


def test_up_down_home_and_end_move_the_cursor_and_clamp() -> None:
    assert field_of(run_solo(SearchList("R", REPOS), "down", "down")).cursor == 2
    assert field_of(run_solo(SearchList("R", REPOS), "up")).cursor == 0
    assert field_of(run_solo(SearchList("R", REPOS), "end")).cursor == 4
    assert field_of(run_solo(SearchList("R", REPOS), "end", "down")).cursor == 4
    assert field_of(run_solo(SearchList("R", REPOS), "end", "home")).cursor == 0


def test_enter_and_an_empty_space_toggle_in_a_multi_list() -> None:
    run = run_solo(SearchList("R", REPOS, multi=True), "enter", "down", "down", " ")

    assert field_of(run).value == ("acme/api", "acme/web")  # item order
    assert run.model.unhandled == ()
    off = run_solo(SearchList("R", REPOS, multi=True), "enter", "enter")
    assert field_of(off).value == ()


def test_toggled_rows_show_the_checked_symbol_and_the_selected_count() -> None:
    run = run_solo(SearchList("R", REPOS, multi=True), "enter", "down", "enter")

    assert "[✓] acme/api" in run.frame
    assert "[ ] acme/docs" in run.frame
    assert "2 selected" in run.frame


def test_in_a_multi_list_a_space_after_text_is_part_of_the_query_not_a_toggle() -> None:
    run = run_solo(SearchList("R", REPOS, multi=True), "a", " ")

    assert field_of(run).query == "a "
    assert field_of(run).selected == frozenset()


def test_a_single_list_picks_one_and_marks_it() -> None:
    run = run_solo(SearchList("R", REPOS), "down", "enter")

    assert field_of(run).value == "acme/api-gateway"
    assert "▶ acme/api-gateway" in run.frame
    again = run_solo(SearchList("R", REPOS), "down", "enter", "up", "enter")
    assert again.model.unhandled == ()
    assert field_of(again).value == "acme/api"  # a new pick replaces the old one


def test_enter_on_the_item_already_picked_is_left_to_the_form() -> None:
    run = run_solo(SearchList("R", REPOS), "enter", "enter")

    assert field_of(run).value == "acme/api"
    assert len(run.model.unhandled) == 1


def test_enter_with_nothing_to_pick_is_left_to_the_form() -> None:
    run = run_solo(SearchList("R", REPOS), *"zzz", "enter")

    assert len(run.model.unhandled) == 1


def test_esc_clears_a_non_empty_query_and_then_backs_out() -> None:
    cleared = run_solo(SearchList("R", REPOS), *"api", "esc")
    assert field_of(cleared).query == ""
    assert cleared.outcome is None
    assert len(_labels(cleared)) == 5

    assert run_solo(SearchList("R", REPOS), *"api", "esc", "esc").outcome == Cancel()
    assert run_solo(SearchList("R", REPOS), "esc").outcome == Cancel()


@pytest.mark.parametrize("key", ["left", "right", "tab", "shift-tab", "ctrl-r", "delete", "esc"])
def test_a_key_that_changes_nothing_returns_the_same_object(key: str) -> None:
    component = SearchList("R", REPOS)

    assert component.update(Key(key))[0] is component


def test_other_messages_and_motion_at_the_edges_return_the_same_object() -> None:
    component = SearchList("R", REPOS)

    assert component.update("hello")[0] is component
    assert component.update(Key("up"))[0] is component
    assert component.update(Key("ctrl-u"))[0] is component  # an empty query stays empty
    empty = SearchList("R", ())
    assert empty.update(Key("down"))[0] is empty
    assert empty.update(Key("enter"))[0] is empty


def test_an_edit_clears_a_stale_error() -> None:
    component = SearchList("R", REPOS).with_error("Pick one.")

    assert component.update(Key("a"))[0].error == ""
    assert component.update(Key("down"))[0].error == "Pick one."
    assert component.validate() == ""


# --- long lists --------------------------------------------------------------------


def test_a_long_list_builds_only_the_rows_in_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[str] = []
    real = lists.item_row

    def counting(frame, inner, item, **kwargs):  # type: ignore[no-untyped-def]
        built.append(item.id)
        return real(frame, inner, item, **kwargs)

    monkeypatch.setattr(lists, "item_row", counting)

    run_solo(SearchList("All", MANY), *["down"] * 5)

    assert built
    assert set(built) <= {f"item-{n:04d}" for n in range(10)}  # never a row past the window


def test_the_window_follows_the_cursor_through_two_thousand_items() -> None:
    run = run_solo(SearchList("All", MANY), "end")

    assert "item 1999" in run.frame
    assert "item 0000" not in run.frame
    assert len(lines(run.frame)) <= 20
    middle = run_solo(SearchList("All", MANY), *["down"] * 1000)
    assert "item 1000" in middle.frame
    assert "item 0999" in middle.frame
    assert "item 1004" in middle.frame


def test_a_long_list_filters_and_ranks_once_per_key(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = lists.rank

    def counting(query, items):  # type: ignore[no-untyped-def]
        calls.append(query)
        return real(query, items)

    monkeypatch.setattr(lists, "rank", counting)
    lists._RANKINGS.clear()

    run_solo(SearchList("All", MANY), *"0042")

    assert calls == ["", "0", "00", "004", "0042"]
    assert field_of(run_solo(SearchList("All", MANY), *"1999")).matches[0].item.id == "item-1999"


def test_the_height_stays_put_while_the_query_narrows_the_list() -> None:
    wide = run_solo(SearchList("R", REPOS))
    narrow = run_solo(SearchList("R", REPOS), *"web")

    assert len(wide.frame.splitlines()) == len(narrow.frame.splitlines())


def _rows(component: SearchList, height: int) -> int:
    """The item rows (and padding) drawn between the divider and the count line."""
    text = "".join(segment.text for segment in _view(component, height=height))
    body = text.splitlines()
    divider = next(n for n, line in enumerate(body) if n and "───" in line)
    return len(body) - divider - 3  # the count line and the bottom edge close the box


def test_a_tall_frame_shows_at_most_ten_rows_and_a_short_one_never_fewer_than_three() -> None:
    assert _rows(SearchList("All", MANY), 60) == lists.MAX_ROWS
    assert _rows(SearchList("All", MANY), 12) == 12 - lists._CHROME
    assert _rows(SearchList("All", MANY), 5) == lists.MIN_ROWS
    assert _rows(SearchList("All", MANY), 1) == lists.MIN_ROWS


def test_a_frame_that_fits_only_the_minimum_rows_shows_exactly_those() -> None:
    assert _rows(SearchList("All", MANY), lists._CHROME + lists.MIN_ROWS) == lists.MIN_ROWS


def test_a_narrowing_query_pads_to_the_same_height_in_any_frame() -> None:
    for height in (5, 12, 60):
        wide = _rows(SearchList("All", MANY), height)
        narrow = _rows(SearchList("All", MANY, query="0042"), height)
        assert narrow == wide


# --- look --------------------------------------------------------------------------


def test_the_label_sits_in_the_border_and_a_note_follows_the_counts() -> None:
    run = run_solo(SearchList("Repos", REPOS, multi=True, note="refreshing", help="Pick some."))

    assert lines(run.frame)[0].startswith("╭─ Repos")
    assert "5 · refreshing" in run.frame
    assert "Pick some." in run.frame


def test_an_error_draws_a_red_border_and_message() -> None:
    segments = _view(SearchList("R", REPOS).with_error("Pick one."))

    assert style_of(segments, "╭").color == role(DEFAULT, "screen.error").color
    assert "Pick one." in "".join(s.text for s in segments)


def test_the_placeholder_shows_in_an_empty_search_line() -> None:
    assert "type to filter" in run_solo(SearchList("R", REPOS), focused=False).frame
    assert "find it" in run_solo(SearchList("R", REPOS, placeholder="find it")).frame


def test_without_a_box_the_list_is_its_label_search_rows_and_counts() -> None:
    run = run_solo(SearchList("Repos", REPOS), theme=BUILTIN_THEMES["quiet"])

    assert lines(run.frame)[0] == "Repos"
    assert lines(run.frame)[1].startswith("▶") is False
    assert not any(char in run.frame for char in "╭│╰")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", [(100, 30), (60, 20), (24, 10)])
def test_every_builtin_theme_renders_the_list(theme: str, size: tuple[int, int]) -> None:
    for component in (
        SearchList("Repos", REPOS, query="api", multi=True, note="n"),
        SearchList("All", MANY, cursor=1500),
        SearchList("Empty", ()),
    ):
        for focused in (True, False):
            run = run_solo(component, theme=BUILTIN_THEMES[theme], size=size, focused=focused)
            assert all(len(line) <= size[0] for line in run.frame.splitlines())


def test_plain_search_list_frames_are_pure_ascii() -> None:
    for component in (SearchList("R", REPOS, query="api"), SearchList("R", REPOS, multi=True)):
        assert run_solo(component, theme=BUILTIN_THEMES["plain"]).frame.isascii()


def test_search_list_is_marked_experimental() -> None:
    assert isinstance(function_mark(SearchList), Experimental)
