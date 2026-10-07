"""A plugin's view: a screen built only from ``untaped.sdk`` names, run through ``ui.run``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from rich.console import Group
from rich.text import Text

from untaped.sdk import (
    Binding,
    Button,
    Buttons,
    Cancel,
    Check,
    Cmd,
    Frame,
    Key,
    NextField,
    Paste,
    Pressed,
    Quit,
    Screen,
    SecretInput,
    TextInput,
    UiContext,
    field_for,
)
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


@dataclass(frozen=True)
class FormModel:
    url: TextInput
    token: SecretInput
    buttons: Buttons
    focus: int = 0


def _form_update(model: FormModel, message: object) -> tuple[FormModel, Sequence[Cmd]]:
    if isinstance(message, Pressed):
        return model, [Cmd.send(Quit((model.url.value, model.token.value.get_secret_value())))]
    names = ("url", "token", "buttons")
    if isinstance(message, NextField):
        return replace(model, focus=min(model.focus + 1, 2)), []
    field = getattr(model, names[model.focus])
    updated, cmds = field.update(message)
    if updated is field and not cmds:
        return model, []
    return replace(model, **{names[model.focus]: updated}), cmds


def _form_view(model: FormModel, frame: Frame) -> Group:
    return Group(
        model.url.view(frame, focused=model.focus == 0, width=40),
        model.token.view(frame, focused=model.focus == 1, width=40),
        model.buttons.view(frame, focused=model.focus == 2),
    )


FORM: Screen[FormModel, tuple[str, str]] = Screen(
    init=lambda: (
        FormModel(
            TextInput("Base URL", help="The controller address."),
            SecretInput("Token"),
            Buttons((Button("save", "Save", "primary"), Button("cancel", "Cancel", "ghost"))),
        ),
        [],
    ),
    update=_form_update,
    view=_form_view,
    title="Configure",
    command="untaped acme configure",
    alternative="untaped acme configure --url URL",
)


def test_a_plugin_builds_a_form_from_sdk_components_only() -> None:
    keys = [*"https://x.test", "tab", *"s3cr3t", "tab", "enter"]
    run = drive_screen(FORM, keys)

    assert run.result == ("https://x.test", "s3cr3t")
    assert all("s3cr3t" not in frame for frame in run.frames)
    assert "Base URL" in run.frames[0]
    assert "Save" in run.frames[0]


def test_a_plugin_maps_a_setting_to_a_component_with_field_for() -> None:
    from untaped.config_schema import walk_settings
    from untaped.settings import HttpSettings

    verify = next(d for d in walk_settings(HttpSettings) if d.key == "verify_ssl")
    check = field_for(verify, help="Check the certificate.")

    assert isinstance(check, Check)
    assert drive_screen(_solo_screen(check)).frames[0].count("✓") == 1


def _solo_screen(component: Check) -> Screen[Check, None]:
    return Screen(
        init=lambda: (component, []),
        update=lambda model, message: model.update(message),
        view=lambda model, frame: model.view(frame, focused=True, width=30),
        title="Check",
        command="untaped acme check",
        alternative="untaped acme check --flag",
    )
