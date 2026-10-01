"""Rendering a picker state to text, checked without a terminal."""

from __future__ import annotations

from untaped.picker import PickCatalog, PickItem, PickRequest, PickSetting
from untaped.picker.state import PickerState, handle, initial_state
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


def _text(state: PickerState, width: int = 120) -> str:
    return "".join(text for _style, text in render(state, width))


def _lines(state: PickerState, width: int = 120) -> list[str]:
    return _text(state, width).split("\n")


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
