"""``Tags``: removable badges, the add menu, and the keys it leaves to the screen."""

from __future__ import annotations

import pytest

from screen.gallery import field_of, lines, render_styled, role, run_solo, style_of
from untaped.screen.components.choices import ListItem
from untaped.screen.components.form import Form, Submitted
from untaped.screen.components.inputs import TextInput
from untaped.screen.components.lists import SearchList, Tags
from untaped.screen.core import Cancel, Frame, Key
from untaped.stability import Experimental, function_mark
from untaped.theme import BUILTIN_THEMES

THEMES = sorted(BUILTIN_THEMES)
DEFAULT = BUILTIN_THEMES["default"]
CAPS = tuple(ListItem(name, name) for name in ("awx", "jira", "github", "ansible", "workspace"))


def _tags(**kwargs: object) -> Tags:
    return Tags("Plugins", CAPS, **kwargs)  # type: ignore[arg-type]


# --- drawing -----------------------------------------------------------------------


def test_the_chosen_items_are_badges_with_a_remove_mark_and_an_add_hint() -> None:
    run = run_solo(_tags(selected=("awx", "jira")))

    body = lines(run.frame)
    assert body[0].startswith("╭─ Plugins")
    assert "awx ✕  jira ✕  + add" in body[1]
    assert "github" not in run.frame  # unchosen items are not shown until the menu opens


def test_an_empty_field_shows_only_the_add_hint() -> None:
    assert "│ + add" in run_solo(_tags()).frame


def test_a_badge_shows_the_label_and_an_unknown_id_shows_as_it_is() -> None:
    items = (ListItem("a1", "Alpha"),)
    run = run_solo(Tags("T", items, selected=("a1", "gone")))

    assert "Alpha ✕" in run.frame
    assert "gone ✕" in run.frame


def test_badges_are_bold_and_the_remove_mark_and_hint_are_muted() -> None:
    segments = render_styled(
        _tags(selected=("awx",)).view(Frame(60, 20, DEFAULT), width=40), width=40
    )

    assert style_of(segments, "awx").bold
    assert style_of(segments, "✕").color == role(DEFAULT, "screen.muted").color
    assert style_of(segments, "+ add").color == role(DEFAULT, "screen.muted").color
    focused = render_styled(_tags().view(Frame(60, 20, DEFAULT), focused=True, width=40), width=40)
    assert style_of(focused, "+ add").color == role(DEFAULT, "screen.accent").color


def test_badges_wrap_inside_the_box_and_a_long_run_is_cut_with_the_ellipsis() -> None:
    items = tuple(ListItem(f"item-{n}", f"item-{n}") for n in range(40))
    run = run_solo(Tags("T", items, selected=tuple(item.id for item in items)), width=30)

    body = lines(run.frame)
    assert all(len(line) <= 30 for line in body)
    assert len(body) <= 3 + 2  # three badge lines inside the box
    assert "…" in run.frame


def test_a_label_wider_than_the_box_is_cut() -> None:
    items = (ListItem("x", "x" * 80),)
    run = run_solo(Tags("T", items, selected=("x",)), width=24)

    assert all(len(line) <= 24 for line in run.frame.splitlines())
    assert "…" in run.frame


# --- removing and adding -----------------------------------------------------------


def test_backspace_and_delete_remove_the_last_badge() -> None:
    run = run_solo(_tags(selected=("awx", "jira", "github")), "backspace")
    assert field_of(run).value == ("awx", "jira")

    run = run_solo(_tags(selected=("awx", "jira")), "delete", "delete", "delete")
    assert field_of(run).value == ()
    assert field_of(run).error == ""


def test_enter_opens_the_menu_of_the_items_not_chosen_yet() -> None:
    run = run_solo(_tags(selected=("awx",)), "enter")

    assert field_of(run).menu_open
    assert run.model.unhandled == ()
    assert all(name in run.frame for name in ("jira", "github", "ansible", "workspace"))
    assert "type to filter" in run.frame
    assert lines(run.frame)[1].startswith("│ awx ✕")


def test_the_menu_offers_only_the_items_not_chosen_yet() -> None:
    opened = field_of(run_solo(_tags(selected=("awx", "jira")), "enter"))

    assert [item.id for item in opened.menu.items] == ["github", "ansible", "workspace"]
    assert all(entry.item.id not in ("awx", "jira") for entry in opened.menu.matches)


def test_enter_in_an_open_menu_with_no_match_is_kept_so_a_form_does_not_submit() -> None:
    form = Form((("name", TextInput("Name", "x")), ("caps", _tags())), focus=1)
    run = run_solo(form, "enter", *"zzz", "enter")

    assert not [m for m in run.model.seen if isinstance(m, Submitted)]
    assert not run.model.unhandled
    assert field_of(run).fields[1][1].menu_open


def test_tab_in_an_open_menu_closes_it_and_then_moves_focus_on() -> None:
    form = Form((("caps", _tags()), ("name", TextInput("Name", "x"))))
    run = run_solo(form, "enter", "tab")

    assert field_of(run).focus == 1
    assert not field_of(run).fields[0][1].menu_open
    back = run_solo(form, "enter", "tab", "shift-tab")
    assert field_of(back).focus == 0
    assert not field_of(back).fields[0][1].menu_open
    assert "github" not in back.frame  # the menu is not drawn again on return


def test_shift_tab_in_an_open_menu_closes_it_and_moves_focus_back() -> None:
    form = Form((("name", TextInput("Name", "x")), ("caps", _tags())), focus=1)
    run = run_solo(form, "enter", "shift-tab")

    assert field_of(run).focus == 0
    assert not field_of(run).fields[1][1].menu_open


def test_the_menu_filters_and_enter_adds_a_badge_and_closes_it() -> None:
    run = run_solo(_tags(selected=("awx",)), "enter", *"git", "enter")

    assert field_of(run).value == ("awx", "github")
    assert not field_of(run).menu_open
    assert "github ✕" in run.frame
    assert "ansible" not in run.frame


def test_added_badges_keep_the_order_they_were_added_in() -> None:
    run = run_solo(_tags(), "enter", *"jira", "enter", "enter", *"awx", "enter")

    assert field_of(run).value == ("jira", "awx")


def test_esc_clears_the_menu_query_then_closes_the_menu_then_goes_back() -> None:
    run = run_solo(_tags(), "enter", *"gi", "esc")
    assert field_of(run).menu_open
    assert field_of(run).menu.query == ""

    run = run_solo(_tags(), "enter", *"gi", "esc", "esc")
    assert not field_of(run).menu_open
    assert run.outcome is None

    assert run_solo(_tags(), "enter", "esc", "esc").outcome == Cancel()


def test_the_menu_is_drawn_only_while_the_field_has_focus() -> None:
    quiet = run_solo(_tags(menu_open=True), focused=False)

    assert "github" not in quiet.frame
    assert field_of(quiet).menu_open  # still open, like a Select
    assert "github" in run_solo(_tags(menu_open=True)).frame


def test_a_menu_open_at_construction_is_built_and_a_closed_field_drops_any_menu() -> None:
    assert isinstance(_tags(menu_open=True).menu, SearchList)
    assert _tags(menu_open=False, menu=SearchList("", CAPS)).menu is None


def test_with_every_item_chosen_enter_has_nothing_to_open_and_goes_to_the_form() -> None:
    everything = tuple(item.id for item in CAPS)
    run = run_solo(_tags(selected=everything), "enter")

    assert not field_of(run).menu_open
    assert len(run.model.unhandled) == 1


# --- unhandled keys ----------------------------------------------------------------


@pytest.mark.parametrize("key", ["left", "right", "tab", "a", "esc", "up", "ctrl-s", "space"])
def test_keys_the_closed_field_has_no_use_for_return_the_same_object(key: str) -> None:
    tags = _tags(selected=("awx",))

    assert tags.update(Key(" " if key == "space" else key))[0] is tags


def test_backspace_on_no_badges_and_other_messages_return_the_same_object() -> None:
    tags = _tags()

    assert tags.update(Key("backspace"))[0] is tags
    assert tags.update("text")[0] is tags
    assert tags.validate() == ""


def test_an_open_menu_hands_back_the_same_object_for_a_key_it_ignores() -> None:
    opened = _tags(menu_open=True)

    assert opened.update(Key("left"))[0] is opened
    assert opened.update(Key("tab"))[0] is opened


def test_enter_in_an_open_menu_with_nothing_to_pick_is_consumed() -> None:
    opened = _tags(menu_open=True).update(Key("z"))[0]
    after = opened.update(Key("enter"))[0]

    assert after == opened
    assert after.menu_open


def test_an_edit_clears_a_stale_error() -> None:
    tags = _tags(selected=("awx",)).with_error("Pick two.")

    assert tags.update(Key("backspace"))[0].error == ""
    assert tags.update(Key("enter"))[0].error == "Pick two."


# --- look --------------------------------------------------------------------------


def test_help_and_error_follow_the_box() -> None:
    assert "Choose some." in run_solo(_tags(help="Choose some.")).frame
    segments = render_styled(
        _tags().with_error("Nope.").view(Frame(60, 20, DEFAULT), width=40), width=40
    )
    assert style_of(segments, "Nope.").color == role(DEFAULT, "screen.error").color


def test_the_plain_theme_uses_ascii_for_the_badge_marks() -> None:
    run = run_solo(_tags(selected=("awx",), menu_open=True), theme=BUILTIN_THEMES["plain"])

    assert "awx x  + add" in run.frame
    assert run.frame.isascii()


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", [(100, 30), (60, 20), (24, 10)])
def test_every_builtin_theme_renders_the_field(theme: str, size: tuple[int, int]) -> None:
    for component in (
        _tags(selected=("awx", "jira")),
        _tags(selected=("awx",), menu_open=True),
        _tags(),
    ):
        for focused in (True, False):
            run = run_solo(component, theme=BUILTIN_THEMES[theme], size=size, focused=focused)
            assert all(len(line) <= size[0] for line in run.frame.splitlines())


def test_tags_is_marked_experimental() -> None:
    assert isinstance(function_mark(Tags), Experimental)
