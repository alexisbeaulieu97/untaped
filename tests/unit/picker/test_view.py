"""Rendering a picker state to text, checked without a terminal."""

from __future__ import annotations

from rich.cells import cell_len

from untaped.picker import PickCatalog, PickItem, PickRequest, PickSetting
from untaped.picker.state import (
    PickerState,
    begin_refresh,
    handle,
    initial_state,
    refresh_failed,
    with_catalog,
)
from untaped.picker.view import render

ITEMS = tuple(PickItem(id=f"acme/r{i:02}", label=f"acme/r{i:02}") for i in range(30))
SETTINGS = (
    PickSetting(key="mode", label="mode", default="write", choices=("write", "read-only")),
    PickSetting(key="base", label="base", placeholder="default"),
)


def _state() -> PickerState:
    return initial_state(
        PickRequest(
            heading="New workspace",
            catalog=PickCatalog(ITEMS, note="refreshed 2h ago"),
            settings=SETTINGS,
            title="JIRA-1",
            subtitle=lambda title, _defaults: f"feature/{title}",
        )
    )


def _text(state: PickerState, width: int = 120, height: int = 40) -> str:
    return "".join(text for _style, text in render(state, width, height))


def _lines(state: PickerState, width: int = 120, height: int = 40) -> list[str]:
    return _text(state, width, height).split("\n")


def test_every_line_fits_the_width() -> None:
    for width in (60, 99, 100, 140):
        assert all(len(line) <= width for line in _lines(_state(), width)), width


def test_wide_terminals_put_the_panes_side_by_side() -> None:
    lines = _lines(_state(), 120)
    assert any("Search" in line and "Selected 0" in line for line in lines)


def test_narrow_terminals_stack_the_panes() -> None:
    lines = _lines(_state(), 80)
    search = next(i for i, line in enumerate(lines) if "Search" in line)
    selected = next(i for i, line in enumerate(lines) if "Selected 0" in line)
    assert selected > search


def test_header_shows_the_heading_title_and_subtitle() -> None:
    assert "New workspace" in _lines(_state())[0]
    assert "JIRA-1" in _lines(_state())[0]
    assert "feature/JIRA-1" in _lines(_state())[0]


def test_list_is_windowed_around_the_cursor() -> None:
    state = _state()
    for _ in range(15):
        state = handle(state, "down")
    text = _text(state)
    assert "acme/r14" in text
    assert "acme/r00" not in text


def test_selected_items_are_marked_and_listed_on_the_right() -> None:
    state = handle(handle(_state(), "down"), " ")
    text = _text(state)
    assert "◉ acme/r00" in text
    assert "Selected 1" in text


def test_inherited_fields_say_so() -> None:
    state = handle(handle(_state(), "down"), " ")
    for key in ("tab", "down", "down", "down"):
        state = handle(state, key)
    assert "· inherit" in _text(state)


def test_footer_shows_the_note_and_the_key_hints() -> None:
    text = _text(_state())
    assert "30 · refreshed 2h ago" in text
    assert "ctrl-s create" in text


def test_errors_replace_the_key_hints() -> None:
    state = handle(_state(), "ctrl-s")
    assert "select at least one item" in _text(state)


def test_quit_prompt_replaces_the_key_hints() -> None:
    state = handle(handle(handle(_state(), "down"), " "), "ctrl-c")
    assert "discard 1 selected? y/n" in _text(state)


def test_the_picker_fits_the_terminal_height() -> None:
    selecting = handle(handle(_state(), "down"), " ")
    for state in (_state(), selecting, handle(selecting, "tab")):
        for width, height in ((80, 24), (100, 24), (120, 30), (80, 15)):
            assert len(_lines(state, width, height)) <= height, (width, height)


def test_a_short_terminal_keeps_create_and_the_footer_visible() -> None:
    selecting = handle(handle(_state(), "down"), " ")
    for state in (_state(), handle(selecting, "tab")):
        lines = _lines(state, 80, 24)
        assert any("[ Create ]" in line for line in lines)
        assert "ctrl-c quit" in lines[-1]


def test_key_hints_fit_80_columns() -> None:
    assert "ctrl-c quit" in _text(_state(), 80)


def test_a_failed_refresh_stays_marked_until_one_succeeds() -> None:
    failed = handle(refresh_failed(begin_refresh(_state()), "HTTP 503"), "down")
    assert "30 · refreshed 2h ago · refresh failed" in _text(failed)
    recovered = with_catalog(begin_refresh(failed), PickCatalog(ITEMS, note="just now"))
    assert "refresh failed" not in _text(recovered)


def test_wide_characters_are_measured_in_terminal_cells() -> None:
    wide = PickItem(id="wide", label="界" * 40, description="説明")
    request = PickRequest(
        heading="New workspace", catalog=PickCatalog((wide, *ITEMS)), settings=SETTINGS
    )
    state = handle(handle(handle(initial_state(request), "down"), " "), "tab")
    for width in (100, 80):
        lines = _lines(state, width)
        assert all(cell_len(line) in (0, width) for line in lines), width  # 0: the blank line


def test_narrow_terminals_render_at_their_real_width() -> None:
    state = handle(handle(_state(), "down"), " ")
    for width in range(12, 41):
        lines = _lines(state, width)
        assert all(cell_len(line) <= width for line in lines), width
        assert any("Create" in line for line in lines), width
        assert not any("Creat…" in line for line in lines), width
