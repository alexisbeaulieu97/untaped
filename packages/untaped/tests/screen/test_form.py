"""``Form``: focus order, per-field validation, submit, and the keys its fields keep."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from screen.gallery import field_of, lines, render_styled, role, run_solo, style_of
from untaped.screen.components.buttons import Button, Buttons, Pressed
from untaped.screen.components.choices import Check, ListItem, Select, SingleList
from untaped.screen.components.fields import Field
from untaped.screen.components.form import Form, Submitted
from untaped.screen.components.inputs import NumberInput, SecretInput, TextInput
from untaped.screen.components.tabs import Tab, Tabs
from untaped.screen.core import Activate, Cancel, Frame, Key, NextField, PrevField, Submit
from untaped.stability import Experimental, function_mark
from untaped.theme import BUILTIN_THEMES

THEMES = sorted(BUILTIN_THEMES)
DEFAULT = BUILTIN_THEMES["default"]


def _url(value: str = "https://x.test") -> TextInput:
    return TextInput(
        "Base URL", value, validator=lambda text: "" if text.startswith("http") else "Needs http."
    )


def _form(**kwargs: object) -> Form:
    fields: tuple[tuple[str, Field], ...] = (
        ("url", _url()),
        ("timeout", NumberInput("Timeout", "30", minimum=1, maximum=600)),
        ("verify", Check("Verify TLS", True)),
    )
    return Form(fields, **kwargs)  # type: ignore[arg-type]


def _submitted(run) -> list[Submitted]:  # type: ignore[no-untyped-def]
    return [message for message in run.model.seen if isinstance(message, Submitted)]


# --- focus order -------------------------------------------------------------------


def test_tab_cycles_fields_and_shift_tab_reverses() -> None:
    assert field_of(run_solo(_form())).focus == 0
    assert field_of(run_solo(_form(), "tab")).focus == 1
    assert field_of(run_solo(_form(), "tab", "tab")).focus == 2
    assert field_of(run_solo(_form(), "tab", "tab", "shift-tab")).focus == 1


def test_focus_does_not_wrap_so_the_parent_gets_the_move_at_either_end() -> None:
    last = run_solo(_form(), "tab", "tab", "tab")
    assert field_of(last).focus == 2
    assert last.model.unhandled == (NextField(),)

    first = run_solo(_form(), "shift-tab")
    assert field_of(first).focus == 0
    assert first.model.unhandled == (PrevField(),)


def test_only_the_focused_field_is_drawn_focused() -> None:
    focus = role(DEFAULT, "screen.focus").color
    border = role(DEFAULT, "screen.border").color

    def top(run) -> list:  # type: ignore[no-untyped-def]
        return [s.style.color for s in render_styled(
            field_of(run).view(Frame(60, 30, DEFAULT), focused=True, width=40), width=40
        ) if s.text.startswith("╭")]  # fmt: skip

    assert top(run_solo(_form())) == [focus, border, border]
    assert top(run_solo(_form(), "tab")) == [border, focus, border]


def test_the_focus_index_is_kept_inside_the_fields() -> None:
    assert _form(focus=9).focus == 2
    assert _form(focus=-3).focus == 0
    assert Form(()).focus == 0


def test_field_names_must_be_distinct() -> None:
    with pytest.raises(ValueError, match="distinct field names"):
        Form((("a", TextInput("A")), ("a", TextInput("B"))))


# --- keys go to the focused field first -------------------------------------------


def test_typing_edits_the_focused_field_and_leaves_the_others() -> None:
    run = run_solo(_form(), "tab", "5")

    assert field_of(run).value["timeout"] == 305
    assert field_of(run).value["url"] == "https://x.test"


def test_a_field_consuming_a_key_blocks_the_shared_key() -> None:
    form = Form(
        (
            ("fruit", TextInput("Fruit", complete=lambda text: ["apple"] * (text == "a"))),
            ("other", TextInput("Other")),
        )
    )
    run = run_solo(form, "a", "tab")  # tab accepts the completion before it moves focus

    assert field_of(run).focus == 0
    assert field_of(run).value["fruit"] == "apple"
    assert run.model.unhandled == ()
    assert field_of(run_solo(form, "a", "tab", "tab")).focus == 1


def test_esc_closes_an_open_select_inside_a_form_before_it_goes_back() -> None:
    choices = tuple(ListItem(name, name) for name in ("a", "b"))
    form = Form((("pick", Select("Pick", choices, "a")),))

    run = run_solo(form, "enter", "esc")
    assert run.outcome is None
    assert not field_of(run).fields[0][1].open
    assert run_solo(form, "enter", "esc", "esc").outcome == Cancel()


def test_enter_opens_a_select_before_it_submits() -> None:
    choices = tuple(ListItem(name, name) for name in ("a", "b"))
    form = Form((("pick", Select("Pick", choices, "a")),))

    assert _submitted(run_solo(form, "enter")) == []
    assert _submitted(run_solo(form, "enter", "enter", "enter")) == []  # open, pick, open again
    assert len(_submitted(run_solo(form, "enter", "enter", "ctrl-s"))) == 1


def test_a_key_nobody_uses_leaves_the_same_form() -> None:
    form = _form()

    assert form.update(Key("ctrl-r"))[0] is form
    assert form.update("text")[0] is form
    assert form.update(Key("up"))[0] is form
    assert Form(()).update(Key("a"))[0].fields == ()


# --- validation and submit ---------------------------------------------------------


def test_submit_validates_every_field_and_focuses_the_first_error() -> None:
    form = Form(
        (
            ("ok", TextInput("Fine", "x")),
            ("url", _url("ftp://nope")),
            ("timeout", NumberInput("Timeout", "0", minimum=1, maximum=600)),
        )
    )
    run = run_solo(form, "ctrl-s")

    assert _submitted(run) == []
    assert field_of(run).focus == 1
    errors = {name: field.error for name, field in field_of(run).fields}
    assert errors == {"ok": "", "url": "Needs http.", "timeout": "Must be between 1 and 600."}
    assert "Needs http." in run.frame
    assert "Must be between 1 and 600." in run.frame


def test_a_valid_form_sends_submitted_with_every_value() -> None:
    run = run_solo(_form(), "ctrl-s")

    assert _submitted(run) == [Submitted({"url": "https://x.test", "timeout": 30, "verify": True})]
    assert run.outcome is None  # the screen decides what a submission does


def test_a_second_submit_clears_the_errors_the_first_one_showed() -> None:
    run = run_solo(_form(), "home", "ctrl-u", "ctrl-s", "h", "t", "t", "p", "ctrl-s")

    assert [field.error for _, field in field_of(run).fields] == ["", "", ""]
    assert len(_submitted(run)) == 1


def test_an_error_clears_on_edit_and_validate_alone_sets_nothing() -> None:
    form = Form((("url", _url("ftp://nope")),))

    assert form.validate() == "Needs http."
    assert form.fields[0][1].error == ""
    run = run_solo(form, "ctrl-s", "x")
    assert field_of(run).fields[0][1].error == ""
    assert Form(()).validate() == ""


def test_enter_on_a_text_field_submits_and_on_buttons_presses() -> None:
    form = Form(
        (
            ("url", _url()),
            ("buttons", Buttons((Button("save", "Save", "primary"), Button("cancel", "Cancel")))),
        )
    )

    on_text = run_solo(form, "enter")
    assert len(_submitted(on_text)) == 1

    on_buttons = run_solo(form, "tab", "enter")
    assert _submitted(on_buttons) == []
    assert on_buttons.model.seen == (Pressed("save"),)
    pressed = run_solo(form, "tab", "right", "enter")
    assert pressed.model.seen == (Pressed("cancel"),)


def test_submit_works_from_a_buttons_row_and_leaves_the_buttons_out_of_the_values() -> None:
    form = Form((("url", _url()), ("buttons", Buttons((Button("save", "Save", "primary"),)))))
    run = run_solo(form, "tab", "ctrl-s")

    assert _submitted(run) == [Submitted({"url": "https://x.test"})]
    assert "buttons" not in form.value


def test_a_secret_stays_a_secret_str_in_the_values() -> None:
    form = Form((("token", SecretInput("Token", SecretStr("hunter2"))), ("url", _url())))
    run = run_solo(form, "ctrl-s")

    (submitted,) = _submitted(run)
    assert isinstance(submitted.values["token"], SecretStr)
    assert submitted.values["token"].get_secret_value() == "hunter2"
    assert "hunter2" not in repr(submitted)
    assert "hunter2" not in repr(field_of(run))


def test_a_form_without_fields_submits_empty() -> None:
    run = run_solo(Form(()), "ctrl-s")

    assert _submitted(run) == [Submitted({})]


def test_a_form_level_error_shows_under_the_fields_in_the_error_colour() -> None:
    form = _form().with_error("Check failed: 401.")
    segments = render_styled(form.view(Frame(60, 40, DEFAULT), width=40), width=40)

    assert style_of(segments, "Check failed: 401.").color == role(DEFAULT, "screen.error").color
    assert form.with_error("").error == ""


# --- tabs inside a form ------------------------------------------------------------


def _token_form() -> Form:
    tabs = Tabs(
        "Token source",
        (
            Tab("keychain", "Keychain", (("token", SecretInput("Token")),)),
            Tab("command", "Command", (("command", TextInput("Command")),)),
        ),
    )
    return Form((("url", _url()), ("source", tabs), ("after", TextInput("After"))))


def test_form_with_tabs_walks_header_then_fields_then_next() -> None:
    def tabs_focus(run) -> int:  # type: ignore[no-untyped-def]
        return field_of(run).fields[1][1].focus

    to_tabs = run_solo(_token_form(), "tab")
    assert (field_of(to_tabs).focus, tabs_focus(to_tabs)) == (1, 0)  # the header

    inside = run_solo(_token_form(), "tab", "tab")
    assert (field_of(inside).focus, tabs_focus(inside)) == (1, 1)  # the tab's own field

    past = run_solo(_token_form(), "tab", "tab", "tab")
    assert field_of(past).focus == 2  # then the next field of the form

    back = run_solo(_token_form(), "tab", "tab", "tab", "shift-tab", "shift-tab", "shift-tab")
    assert (field_of(back).focus, tabs_focus(back)) == (0, 0)


def test_the_active_tabs_fields_are_in_the_form_value() -> None:
    run = run_solo(_token_form(), "tab", "right")

    assert field_of(run).value["source"] == {"tab": "command", "command": ""}


# --- long forms --------------------------------------------------------------------


def test_a_form_taller_than_the_frame_keeps_the_focused_field_in_view() -> None:
    form = Form(tuple((f"f{n}", TextInput(f"Field {n}")) for n in range(12)))
    run = run_solo(form, *["tab"] * 11, size=(50, 14))

    assert all(len(frame.splitlines()) <= 14 for frame in run.frames)
    for number, frame in enumerate(run.frames[:12]):
        assert f"Field {number}" in frame, (number, frame)
    assert "Field 0" not in run.frames[-1]


def test_a_form_that_fits_draws_every_field_with_a_blank_line_between() -> None:
    run = run_solo(Form((("a", TextInput("A")), ("b", TextInput("B")))), size=(30, 20))

    body = run.frame.splitlines()
    assert body[0].startswith("╭─ A")
    assert body[2].startswith("╰")
    assert body[3] == ""
    assert body[4].startswith("╭─ B")


def test_validate_reports_the_first_field_error_without_setting_it() -> None:
    form = Form((("pick", SingleList("Pick", (ListItem("a", "a"),))), ("n", NumberInput("N", "x"))))

    assert form.validate() == "Must be a whole number."
    assert Form((("pick", SingleList("Pick", (ListItem("a", "a"),))),)).validate() == ""


# --- look --------------------------------------------------------------------------


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", [(100, 30), (60, 20), (24, 10)])
def test_every_builtin_theme_renders_the_form(theme: str, size: tuple[int, int]) -> None:
    for form in (_form(), _token_form(), Form(())):
        for focused in (True, False):
            run = run_solo(form, "tab", theme=BUILTIN_THEMES[theme], size=size, focused=focused)
            assert all(len(line) <= size[0] for line in run.frame.splitlines())
            assert len(run.frame.splitlines()) <= size[1]


def test_a_plain_form_frame_is_pure_ascii() -> None:
    run = run_solo(_form().with_error("Nope."), "ctrl-s", theme=BUILTIN_THEMES["plain"])

    assert run.frame.isascii()


def test_without_a_box_the_fields_are_label_value_and_help_lines() -> None:
    run = run_solo(_form(), theme=BUILTIN_THEMES["quiet"])

    assert lines(run.frame)[:2] == ["Base URL", "https://x.test"]


def test_activate_and_submit_are_not_treated_as_keys() -> None:
    form = _form()

    assert form.update(Activate())[1][0].fn() == Submitted(form.value)
    assert form.update(Submit())[1][0].fn() == Submitted(form.value)


def test_form_and_submitted_are_marked_experimental() -> None:
    assert isinstance(function_mark(Form), Experimental)
    assert isinstance(function_mark(Submitted), Experimental)
