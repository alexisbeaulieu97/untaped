"""``drive_screen`` runs a screen without a terminal and returns what a user would see."""

from __future__ import annotations

import contextvars
import copy
import pickle
from dataclasses import dataclass, replace

import pytest
from rich.text import Text

from screen.support import make_screen
from untaped.screen.core import Binding, Cancel, Cmd, Frame, Key, Paste, Quit, Resize, Screen
from untaped.stability import Experimental, function_mark
from untaped.testing import ScreenKeys, ScreenRun, drive_screen
from untaped.testing.screens import rendered_text
from untaped.theme import BUILTIN_THEMES


@dataclass(frozen=True)
class Counter:
    count: int = 0
    notes: tuple[str, ...] = ()
    width: int = 0


@dataclass(frozen=True)
class Loaded:
    text: str


def _update(model: Counter, message: object) -> tuple[Counter, list[Cmd]]:
    match message:
        case Key("up"):
            return replace(model, count=model.count + 1), []
        case Key("l"):
            return model, [Cmd(lambda: Loaded("from disk"), name="load")]
        case Key("s"):
            return model, [Cmd(lambda: Loaded("saved"), write=True, name="save")]
        case Key("q"):
            return model, [Cmd.send(Quit(model.count))]
        case Key("x"):
            return model, [Cmd.send(Cancel())]
        case Paste(text):
            return replace(model, notes=(*model.notes, f"paste:{text}")), []
        case Resize(width, _):
            return replace(model, width=width), []
        case Loaded(text):
            return replace(model, notes=(*model.notes, text)), []
        case _:
            return model, []


def _view(model: Counter, frame: Frame) -> Text:
    text = Text(f"count {model.count} width {model.width}")
    for note in model.notes:
        text.append(f"\n{note}")
    return text


def _screen(**overrides: object) -> Screen[Counter, int]:
    fields: dict[str, object] = {
        "init": lambda: (Counter(), []),
        "update": _update,
        "view": _view,
        "keys": (Binding("l", "load", None),),
    }
    fields.update(overrides)
    return make_screen(**fields)


def test_frames_after_each_key() -> None:
    run = drive_screen(_screen(), ["up", "up", "up"])
    assert len(run.frames) == 4
    assert [frame.splitlines()[0] for frame in run.frames] == [
        "count 0 width 0",
        "count 1 width 0",
        "count 2 width 0",
        "count 3 width 0",
    ]
    assert run.frame == run.frames[-1]
    assert run.model.count == 3


def test_a_frame_is_the_requested_size_with_the_footer_on_the_last_row() -> None:
    run = drive_screen(_screen(), size=(50, 9))
    lines = run.frame.splitlines()
    assert len(lines) == 9
    assert lines[-1] == "l load · esc back · ? help"
    assert all(len(line) <= 50 for line in lines)


def test_a_run_without_keys_shows_the_first_frame() -> None:
    run = drive_screen(_screen())
    assert (len(run.frames), run.outcome, run.result) == (1, None, None)


def test_quit_gives_the_result_and_the_outcome() -> None:
    run = drive_screen(_screen(), ["up", "up", "q"])
    assert run.outcome == Quit(2)
    assert run.result == 2


def test_cancel_gives_no_result() -> None:
    run = drive_screen(_screen(), ["x"])
    assert (run.outcome, run.result) == (Cancel(), None)


def test_esc_cancels_through_the_shared_key() -> None:
    assert drive_screen(_screen(), ["esc"]).outcome == Cancel()
    assert drive_screen(_screen(), ["ctrl-c"]).outcome == Cancel(interrupted=True)


def test_commands_run_synchronously_in_order() -> None:
    order: list[str] = []

    def update(model: Counter, message: object) -> tuple[Counter, list[Cmd]]:
        if message == Key("g"):
            return model, [
                Cmd(lambda: order.append("first"), name="first"),
                Cmd(lambda: order.append("second"), name="second"),
                Cmd(lambda: order.append("third"), write=True, name="third"),
            ]
        return model, []

    run = drive_screen(_screen(update=update), ["g"])
    assert order == ["first", "second", "third"]
    assert run.commands_run == ("first", "second", "third")


def test_a_command_result_is_in_the_frame_after_its_key() -> None:
    run = drive_screen(_screen(), ["l"])
    assert "from disk" in run.frame
    assert "from disk" not in run.frames[0]
    assert run.commands_run == ("load",)


def test_stubbed_commands_by_name() -> None:
    ran: list[str] = []

    def update(model: Counter, message: object) -> tuple[Counter, list[Cmd]]:
        if message == Key("l"):
            return model, [
                Cmd(lambda: ran.append("real load") or Loaded("real"), name="load"),
                Cmd(lambda: Loaded("untouched"), name="other"),
            ]
        if isinstance(message, Loaded):
            return replace(model, notes=(*model.notes, message.text)), []
        return model, []

    run = drive_screen(_screen(update=update), ["l"], commands={"load": Loaded("stubbed")})
    assert run.model.notes == ("stubbed", "untouched")  # other commands still run
    assert ran == []  # the stub replaced the command
    assert run.commands_run == ("load", "other")


def test_a_stub_can_be_a_callable_returning_the_message() -> None:
    run = drive_screen(_screen(), ["l"], commands={"load": lambda: Loaded("computed")})
    assert run.model.notes == ("computed",)


def test_a_stub_returning_none_sends_nothing() -> None:
    run = drive_screen(_screen(), ["l"], commands={"load": lambda: None})
    assert run.model.notes == ()


def test_a_stub_that_matches_no_command_is_an_error_naming_it() -> None:
    with pytest.raises(ValueError, match=r"never run: 'lode'; commands that ran: 'load'"):
        drive_screen(_screen(), ["l"], commands={"load": Loaded("ok"), "lode": Loaded("typo")})


def test_a_stub_for_a_command_that_never_got_issued_is_an_error() -> None:
    with pytest.raises(ValueError, match=r"'load'.*commands that ran: none"):
        drive_screen(_screen(), ["up"], commands={"load": Loaded("never")})


def test_commands_must_be_sync_or_a_mapping() -> None:
    with pytest.raises(ValueError, match="commands"):
        drive_screen(_screen(), [], commands="async")  # type: ignore[arg-type]


@pytest.mark.parametrize("key", ["escape", "Enter", "ctrl-x-y", "space", ""])
def test_unknown_key_name_is_an_error(key: str) -> None:
    with pytest.raises(ValueError, match="unknown key"):
        drive_screen(_screen(), ["up", key])


def test_a_bad_key_fails_before_anything_runs() -> None:
    ran: list[str] = []
    screen = _screen(init=lambda: (ran.append("init") or Counter(), []))
    with pytest.raises(ValueError, match="unknown key"):
        drive_screen(screen, ["typo"])
    assert ran == []


def test_a_single_character_is_a_key_and_a_space_is_the_space_bar() -> None:
    seen: list[object] = []

    def update(model: Counter, message: object) -> tuple[Counter, list[Cmd]]:
        seen.append(message)
        return model, []

    drive_screen(_screen(update=update), ["a", "7", " "])
    assert seen == [Key("a"), Key("7"), Key(" ")]


def test_paste_and_resize_keys() -> None:
    run = drive_screen(_screen(), [Paste("hello"), Resize(40, 7)])
    assert run.model.notes == ("paste:hello",)
    assert run.model.width == 40
    assert "paste:hello" in run.frames[1]
    assert len(run.frames[2].splitlines()) == 7
    assert "width 40" in run.frame


def test_screen_keys_is_a_tuple_script() -> None:
    script = ScreenKeys("up", "up", Paste("x"), Resize(30, 5))
    assert isinstance(script, tuple)
    assert script == ("up", "up", Paste("x"), Resize(30, 5))
    run = drive_screen(_screen(), script)
    assert (run.model.count, run.model.width) == (2, 30)


def test_screen_keys_survive_copy_and_pickle_unchanged() -> None:
    script = ScreenKeys("up", Paste("x"), Resize(30, 5), " ")
    for clone in (copy.copy(script), copy.deepcopy(script), pickle.loads(pickle.dumps(script))):
        assert type(clone) is ScreenKeys
        assert clone == script
        assert len(clone) == 4


def test_drive_screen_is_marked_experimental() -> None:
    assert isinstance(function_mark(drive_screen), Experimental)


def test_commands_start_in_the_order_they_were_issued() -> None:
    def update(model: Counter, message: object) -> tuple[Counter, list[Cmd]]:
        if message == Key("g"):
            return model, [
                Cmd(lambda: None, write=True, name="w"),
                Cmd(lambda: None, name="b"),
            ]
        return model, []

    assert drive_screen(_screen(update=update), ["g"]).commands_run == ("w", "b")


def test_a_write_command_completes_before_the_run_returns() -> None:
    done: list[str] = []

    def update(model: Counter, message: object) -> tuple[Counter, list[Cmd]]:
        if message == Key("s"):
            return model, [
                Cmd(lambda: done.append("written"), write=True, name="write"),
                Cmd.send(Quit("quit while writing")),
            ]
        return model, []

    run = drive_screen(_screen(update=update), ["s"])
    assert done == ["written"]
    assert run.result == "quit while writing"


def test_keys_after_the_screen_ended_are_ignored() -> None:
    run = drive_screen(_screen(), ["up", "q", "up", "up"])
    assert run.result == 1
    assert run.model.count == 1
    assert len(run.frames) == 5


def test_commands_see_the_callers_context() -> None:
    var: contextvars.ContextVar[str] = contextvars.ContextVar("drive_var", default="unset")

    def update(model: Counter, message: object) -> tuple[Counter, list[Cmd]]:
        if message == Key("l"):
            return model, [Cmd(lambda: Loaded(var.get()), name="read")]
        if isinstance(message, Loaded):
            return replace(model, notes=(message.text,)), []
        return model, []

    token = var.set("caller")
    try:
        run = drive_screen(_screen(update=update), ["l"])
    finally:
        var.reset(token)
    assert run.model.notes == ("caller",)


def test_the_theme_changes_the_frame() -> None:
    def view(model: Counter, frame: Frame) -> Text:
        return Text(f"{frame.symbol('chosen')} {frame.ellipsis()}")

    default = drive_screen(_screen(view=view))
    plain = drive_screen(_screen(view=view), theme=BUILTIN_THEMES["plain"])
    assert default.frame.splitlines()[0] == "▶ …"
    assert plain.frame.splitlines()[0] == "> ..."


def test_frames_are_plain_text() -> None:
    def view(model: Counter, frame: Frame) -> Text:
        return Text("bold red", style="bold red")

    assert "\x1b" not in drive_screen(_screen(view=view)).frame


def test_a_view_string_is_literal_in_a_frame() -> None:
    run = drive_screen(_screen(view=lambda model, frame: "[WIP] fix [/] :smile:"))
    assert run.frame.splitlines()[0] == "[WIP] fix [/] :smile:"


def test_rendered_text_is_one_string_with_blanks_trimmed() -> None:
    text = rendered_text(Text("hi"), 10, 3)
    assert text == "hi"


def test_screen_run_is_frozen_and_typed() -> None:
    run = drive_screen(_screen(), ["up"])
    assert isinstance(run, ScreenRun)
    with pytest.raises(AttributeError):
        run.result = 3  # type: ignore[misc]


def test_a_screen_relabels_shared_keys_in_its_footer_and_overlay() -> None:
    screen = make_screen(
        view=lambda model, frame: "body",
        shared_labels={
            "ctrl-s": "create",
            "enter": lambda model: "edit" if model else None,
        },
    )
    quiet = drive_screen(screen)
    assert quiet.frame.splitlines()[-1] == "ctrl-s create · esc back · ? help"

    editing = drive_screen(
        make_screen(
            init=lambda: (1, []),
            view=lambda model, frame: "body",
            shared_labels={"enter": lambda model: "edit" if model else None},
        )
    )
    assert editing.frame.splitlines()[-1] == "enter edit · esc back · ? help"

    overlay = drive_screen(screen, ["?"]).frame
    assert "create" in overlay
    assert "submit" not in overlay
    assert "activate" in overlay


def test_help_is_delivered_to_update_when_the_overlay_opens() -> None:
    from untaped.screen.core import Help

    seen: list[object] = []

    def update(model: int, message: object) -> tuple[int, list[Cmd]]:
        seen.append(message)
        return model, []

    run = drive_screen(make_screen(update=update, view=lambda m, f: "body"), ["?"])
    assert Help() in seen
    assert "Keys" in run.frame
