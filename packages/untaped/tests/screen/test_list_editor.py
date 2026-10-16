"""``ListEditor`` (an ordered list of strings) and ``MapEditor`` (a value per name)."""

from __future__ import annotations

from screen.gallery import field_of, lines, run_solo
from untaped.screen.components.lists import ListEditor, MapEditor, MapEntry
from untaped.screen.core import Key, Paste


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


def test_an_open_item_counts_as_kept_so_ctrl_s_keeps_it() -> None:
    editor = run_solo(_list("a"), "down", "enter", "b")
    refusing = _list(validator=lambda text: "no spaces" if " " in text else "")
    refused = run_solo(refusing, "enter", "a", " ", "b")

    assert field_of(editor).value == ("a", "b")
    assert field_of(editor).validate() == ""
    assert field_of(refused).validate() == "no spaces"
    assert field_of(refused).value == ()


def test_an_item_keeps_its_text_as_typed() -> None:
    run = run_solo(_list(" a "), "enter", "enter")

    assert field_of(run).value == (" a ",)


def test_lines_pasted_into_a_new_item_become_one_item_each() -> None:
    run = run_solo(_list("a"), "down", "enter", Paste("b\n\nc\n"))

    assert field_of(run).value == ("a", "b", "c")
    assert field_of(run).editing is None


def test_a_paste_keeps_what_was_typed_and_names_a_refused_line() -> None:
    typed = run_solo(_list(), "enter", "x", Paste("b\nc"))
    refusing = _list(validator=lambda text: "no spaces" if " " in text else "")
    refused = run_solo(refusing, "enter", Paste("b\nc d"))

    assert field_of(typed).value == ("xb", "c")
    assert field_of(refused).value == ()
    assert field_of(refused).error == "'c d': no spaces"


def test_enter_on_done_and_esc_are_left_to_the_screen() -> None:
    done = _list("a", cursor=2)

    assert done.update(Key("enter"))[0] is done
    assert done.update(Key("esc"))[0] is done


def test_a_long_list_shows_a_window_around_the_cursor() -> None:
    items = tuple(f"org{index:02}" for index in range(30))
    run = run_solo(_list(*items), "end", size=(60, 16))

    assert "done" in run.frame and "org00" not in run.frame


# ``MapEditor``: a fixed set of names, each set here or inherited.


def _map(own: dict[str, str] | None = None, **kwargs: object) -> MapEditor:
    entries = (
        MapEntry("ok", "✓", "theme"),
        MapEntry("fail", "✗", "theme"),
    )
    return MapEditor(
        "Symbols",
        entries,
        tuple((own or {}).items()),
        "profile:work",
        **kwargs,  # type: ignore[arg-type]
    )


def test_each_name_shows_its_value_and_source_then_done() -> None:
    body = run_solo(_map({"fail": "x"})).frame

    assert "✓" in body and "theme" in body
    assert "x" in body and "profile:work" in body
    assert "done" in body


def test_enter_edits_a_name_in_place_and_enter_keeps_it() -> None:
    run = run_solo(_map(), "enter", "backspace", "y", "enter")

    assert field_of(run).value == {"ok": "y"}
    assert field_of(run).editing is None


def test_an_inherited_value_is_not_copied_until_it_is_changed() -> None:
    assert field_of(run_solo(_map(), "enter", "esc")).value == {}
    assert field_of(run_solo(_map(), "enter", "enter")).value == {}


def test_delete_clears_the_name_and_an_emptied_value_is_kept_blank() -> None:
    deleted = run_solo(_map({"ok": "y", "fail": "n"}), "down", "delete")
    emptied = run_solo(_map({"ok": "y"}), "enter", "ctrl-u", "enter")

    assert field_of(deleted).value == {"ok": "y"}
    assert field_of(emptied).value == {"ok": ""}
    assert "(blank)" in emptied.frame


def test_a_value_the_map_sets_says_what_wins_over_it() -> None:
    editor = MapEditor(
        "Symbols", (MapEntry("ok", "V", "UNTAPED_UI__SYMBOLS", wins="UNTAPED_UI__SYMBOLS"),),
        (("ok", "S"),), "profile:work",
    )  # fmt: skip

    assert "profile:work, UNTAPED_UI__SYMBOLS wins" in run_solo(editor).frame


def test_an_open_value_counts_as_kept() -> None:
    assert field_of(run_solo(_map(), "enter", "!")).value == {"ok": "✓!"}


def test_a_name_the_map_does_not_accept_says_unknown() -> None:
    editor = MapEditor("Symbols", (MapEntry("stray", "?", "profile:work", known=False),))

    assert "unknown" in run_solo(editor).frame


def test_enter_on_done_and_esc_are_left_to_the_screen_for_a_map() -> None:
    done = _map(cursor=2)

    assert done.rows == 3
    assert done.update(Key("enter"))[0] is done
    assert done.update(Key("esc"))[0] is done


def test_an_unset_name_says_so_and_kept_empty_is_set_blank() -> None:
    editor = MapEditor("Colors", (MapEntry("success", None, "theme"),), (), "profile:work")

    assert "unset" in run_solo(editor).frame
    assert field_of(run_solo(editor, "enter", "esc")).value == {}
    assert field_of(run_solo(editor, "enter", "enter")).value == {"success": ""}
