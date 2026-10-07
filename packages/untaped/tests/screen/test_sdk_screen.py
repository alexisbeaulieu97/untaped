"""A plugin's view: a screen built only from ``untaped.sdk`` names, run through ``ui.run``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from rich.text import Text

from untaped.sdk import Binding, Cancel, Cmd, Frame, Key, Paste, Quit, Screen, UiContext
from untaped.testing import ScreenKeys, ScriptedPromptBackend, TtyStringIO, drive_screen


@dataclass(frozen=True)
class Model:
    name: str = ""
    shout: bool = False


@dataclass(frozen=True)
class Toggle:
    """What the screen's own ``ctrl-r`` binding sends."""


def _update(model: Model, message: object) -> tuple[Model, Sequence[Cmd]]:
    if isinstance(message, Key):
        if message.name == "enter":
            return model, [Cmd.send(Quit(model.name.upper() if model.shout else model.name))]
        if len(message.name) == 1:
            return replace(model, name=model.name + message.name), []
    if isinstance(message, Paste):
        return replace(model, name=model.name + message.text), []
    if isinstance(message, Toggle):
        return replace(model, shout=not model.shout), []
    return model, []


def _view(model: Model, frame: Frame) -> Text:
    return Text(f"name{frame.symbol('separator')}{model.name}", style=frame.style("screen.value"))


SCREEN: Screen[Model, str] = Screen(
    init=lambda: (Model(), []),
    update=_update,
    view=_view,
    title="Name it",
    command="untaped acme name",
    alternative="untaped acme name --name NAME",
    keys=(Binding("ctrl-r", "shout", Toggle()),),
)


def test_a_plugin_screen_runs_through_ui_run_with_scripted_keys() -> None:
    backend = ScriptedPromptBackend(screens=[ScreenKeys("a", "b", "ctrl-r", "enter")])
    ui = UiContext(stdin=TtyStringIO(), stderr=TtyStringIO(), prompt_backend=backend)

    assert ui.run(SCREEN) == "AB"
    assert backend.ran == [SCREEN]


def test_a_plugin_screen_is_testable_with_drive_screen() -> None:
    run = drive_screen(SCREEN, ["h", "i", Paste(" there")])

    assert "name·hi there" in run.frame
    assert "ctrl-r shout" in run.frame  # the footer is built from the screen's bindings
    assert run.outcome is None


def test_a_plugin_screen_ends_with_cancel_on_back() -> None:
    assert drive_screen(SCREEN, ["esc"]).outcome == Cancel()
