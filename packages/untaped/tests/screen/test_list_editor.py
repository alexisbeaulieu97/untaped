"""``ListEditor``: an ordered list of strings to add to, edit, remove from and reorder."""

from __future__ import annotations

from screen.gallery import field_of, lines, run_solo
from untaped.screen.components.lists import ListEditor
from untaped.screen.core import Key


def _list(*items: str, **kwargs: object) -> ListEditor:
    return ListEditor("Orgs", items, **kwargs)  # type: ignore[arg-type]


def test_the_items_then_add_and_done_are_rows() -> None:
    body = lines(run_solo(_list("acme", "acme-labs")).frame)

    assert [line.strip("│ ") for line in body[1:5]] == ["acme", "acme-labs", "+ add", "done"]


def test_enter_on_add_opens_an_item_and_enter_keeps_it() -> None:
    run = run_solo(_list("acme"), "down", "enter", "l", "a", "b", "s", "enter")

    assert field_of(run).value == ("acme", "labs")
    assert field_of(run).editing is None
    assert field_of(run).cursor == 2  # on ``+ add`` again, ready for the next one


def test_enter_on_an_item_edits_it_and_esc_drops_the_edit() -> None:
    kept = run_solo(_list("acme"), "enter", "backspace", "x", "enter")
    dropped = run_solo(_list("acme"), "enter", "ctrl-u", "x", "esc")

    assert field_of(kept).value == ("acmx",)
    assert field_of(dropped).value == ("acme",)


def test_an_item_emptied_is_removed_and_an_empty_new_one_adds_nothing() -> None:
    emptied = run_solo(_list("acme", "labs"), "enter", "ctrl-u", "enter")
    nothing = run_solo(_list("acme"), "down", "enter", " ", "enter")

    assert field_of(emptied).value == ("labs",)
    assert field_of(nothing).value == ("acme",)


def test_delete_and_backspace_remove_the_item_under_the_cursor() -> None:
    deleted = run_solo(_list("a", "b", "c"), "down", "delete")
    backspaced = run_solo(_list("a", "b", "c"), "backspace")

    assert field_of(deleted).value == ("a", "c")
    assert field_of(backspaced).value == ("b", "c")


def test_m_picks_an_item_up_and_carries_it_until_enter() -> None:
    run = run_solo(_list("a", "b", "c"), "m", "down", "down", "enter", "up")

    assert field_of(run).value == ("b", "c", "a")
    assert field_of(run).moving is False
    assert field_of(run).cursor == 1  # up after enter moves the cursor, not the item


def test_the_item_being_moved_says_so() -> None:
    assert "moving" in run_solo(_list("a", "b"), "m").frame


def test_a_validator_keeps_a_refused_item_open_with_its_error() -> None:
    editor = _list(validator=lambda text: "no spaces" if " " in text else "")
    run = run_solo(editor, "enter", "a", " ", "b", "enter")

    assert field_of(run).value == ()
    assert field_of(run).editing is not None
    assert "no spaces" in run.frame


def test_an_open_item_must_be_kept_or_dropped_before_the_list_is_written() -> None:
    editor, _ = _list("a").update(Key("enter"))

    assert editor.validate() == "Press enter to keep the open item, or esc to drop it."
    assert _list("a").validate() == ""


def test_enter_on_done_and_esc_are_left_to_the_screen() -> None:
    done = _list("a", cursor=2)

    assert done.update(Key("enter"))[0] is done
    assert done.update(Key("esc"))[0] is done


def test_a_long_list_shows_a_window_around_the_cursor() -> None:
    items = tuple(f"org{index:02}" for index in range(30))
    run = run_solo(_list(*items), "end", size=(60, 16))

    assert "done" in run.frame and "org00" not in run.frame
