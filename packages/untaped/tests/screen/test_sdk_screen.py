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
    Cmd,
    Form,
    Frame,
    Key,
    ListItem,
    NextField,
    Panes,
    Paste,
    Pressed,
    Quit,
    Screen,
    SearchList,
    SecretInput,
    Submitted,
    Tags,
    TextInput,
    Tree,
    TreeRow,
    UiContext,
    Viewport,
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


def _panes_screen() -> Screen[Panes, tuple[str, str]]:
    """A two-pane screen from SDK names only: a searchable list beside a form that submits."""
    repos = tuple(ListItem(name, name) for name in ("acme/api", "acme/web", "legacy/docs"))
    form = Form((("branch", TextInput("Branch", "main")), ("tags", Tags("Tags", repos))))

    def update(model: Panes, message: object) -> tuple[Panes, Sequence[Cmd]]:
        if isinstance(message, Submitted):
            return model, [Cmd.send(Quit((str(model.left.value), str(message.values["branch"]))))]
        return model.update(message)

    return Screen(
        init=lambda: (
            Panes(SearchList("", repos), form, left_title="Repos", right_title="Edit"),
            [],
        ),
        update=update,
        view=lambda model, frame: model.view(frame, focused=True),
        title="Repos",
        command="untaped acme repos",
        alternative="untaped acme repos --branch BRANCH",
    )


def test_a_plugin_builds_panes_a_search_list_and_a_form_from_sdk_names() -> None:
    run = drive_screen(_panes_screen(), [*"web", "enter", "tab", *"-2", "ctrl-s"])

    assert run.result == ("acme/web", "main-2")
    assert "Repos" in run.frames[0]
    assert "Edit" in run.frames[0]
    assert "type to filter" in run.frames[0]


def test_a_plugin_can_use_a_tree_and_a_viewport_from_sdk_names() -> None:
    tree = Tree("Settings", (TreeRow("a", "alpha", "x", (TreeRow("b", "beta"),)),))
    view = Viewport(content_height=100)
    run = drive_screen(
        Screen(
            init=lambda: (tree, []),
            update=lambda model, message: model.update(message),
            view=lambda model, frame: model.view(frame, focused=True, width=30),
            title="Tree",
            command="untaped acme tree",
            alternative="untaped acme tree --flag",
        ),
        ["right", "down"],
    )

    assert "▾ alpha" in run.frame
    assert run.model.value == "b"
    assert view.centred(50, 10).start(10) == 45
