"""The setup screen's navigation: every message the screen gets, sent where it belongs.

:func:`update` takes typed keys and pastes, which go to the focused profile
field, list, section rows or form; the keys a field passed on (tab, enter,
ctrl-s, esc, ctrl-c); and what a command brought back. The shared keys move
the focus between the profile field, the list, a section's rows and a form;
leaving the profile field loads the profile typed there, so the field and
the rows never disagree. A row's answers go to
:mod:`~untaped.core.commands.setup_screen_rows`; a form's submit, a button,
a check's or a save's answer and this install's registration to
:mod:`~untaped.core.commands.setup_screen_save`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from untaped.core.commands.setup_connection import reset
from untaped.core.commands.setup_screen_listing import _forms, _panes, _profile_field, row_status
from untaped.core.commands.setup_screen_model import (
    INSTALL_ROW,
    CapRow,
    Focus,
    NotSaved,
    Primed,
    Probed,
    ProfileLoaded,
    Registered,
    Saved,
    SetupModel,
    SetupRun,
    _closed_focus,
    _opened_focus,
    _result,
    _with_pane,
    _without,
)
from untaped.core.commands.setup_screen_rows import _open, _row_answer, _write
from untaped.core.commands.setup_screen_save import (
    _failed,
    _not_saved,
    _pressed,
    _primed,
    _probed,
    _registered,
    _saved,
    _start,
    register_install,
)
from untaped.core.commands.setup_sections import (
    FieldOpened,
    Listing,
    UnsetAsked,
    Written,
    read_listing,
)
from untaped.screen.components.buttons import Pressed
from untaped.screen.components.choices import ListItem, SingleList
from untaped.screen.components.form import Submitted
from untaped.screen.core import (
    Activate,
    Back,
    Cmd,
    CmdError,
    Interrupt,
    KeyPress,
    NextField,
    Paste,
    PrevField,
    Quit,
    Submit,
)


def update(run: SetupRun, model: SetupModel, message: object) -> tuple[SetupModel, list[Cmd]]:
    if isinstance(message, KeyPress | Paste):
        return _edit(model, message)
    if isinstance(message, Activate | Back | Interrupt | NextField | PrevField | Submit):
        return _shared(run, model, message)
    return _answer(run, model, message)


def _shared(
    run: SetupRun,
    model: SetupModel,
    message: Activate | Back | Interrupt | NextField | PrevField | Submit,
) -> tuple[SetupModel, list[Cmd]]:
    """A shared key the focused field passed on."""
    match message:
        case NextField():
            return _next(run, model)
        case PrevField():
            return _previous(run, model)
        case Activate():
            return _activate(run, model)
        case Submit():
            return _submit(run, model)
        case Back():
            return _back(model)
        case Interrupt():
            return _leave(model, "interrupt")


def _answer(run: SetupRun, model: SetupModel, message: object) -> tuple[SetupModel, list[Cmd]]:
    """A form's submit, a button, a row's answer, or what a command brought back."""
    match message:
        case Submitted() if model.name == INSTALL_ROW:
            return register_install(model)
        case Submitted(values=values):
            return _start(run, model, values)
        case Pressed(id=button):
            return _pressed(run, model, button)
        case Primed(name=name):
            return _primed(run, model, name)
        case Probed(name=name, rows=rows):
            return _probed(run, model, name, rows)
        case Saved():
            return _saved(run, model, message)
        case NotSaved():
            return _not_saved(run, model, message)
        case ProfileLoaded(profile=profile, listing=listing):
            return _loaded(run, model, profile, listing)
        case Registered(text=text):
            return _registered(model, text)
        case FieldOpened() | UnsetAsked() | Written():
            return _row_answer(run, model, message)
        case CmdError(error=error):
            return _failed(model, error)
    return model, []


def _edit(model: SetupModel, message: KeyPress | Paste) -> tuple[SetupModel, list[Cmd]]:
    if model.focus == "profile":
        if model.phase != "idle":
            return model, []  # the field and the rows must agree: no typing mid-load or save
        field, cmds = model.profile.update(message)
        return (model if field is model.profile else replace(model, profile=field)), cmds
    if model.focus == "list":
        if not isinstance(message, KeyPress):
            return model, []
        items = tuple(ListItem(row.name, row.name) for row in model.rows)
        moved, _ = SingleList("", items, value=model.name, cursor=model.selected).update(message)
        if moved.cursor == model.selected:
            return model, []
        return replace(model, selected=moved.cursor or 0), []
    if model.focus == "section":
        return _in_pane(model, message)
    return _in_form(model, message)


def _in_pane(model: SetupModel, message: object) -> tuple[SetupModel, list[Cmd]]:
    """Give ``message`` to the selected section's rows (not while a read or write runs)."""
    pane = model.pane
    if pane is None or model.phase != "idle":
        return model, []
    updated, cmds = pane.update(message)
    if updated is pane and not cmds:
        return model, []
    return _with_pane(model, model.name, updated), cmds


def _in_form(model: SetupModel, message: object) -> tuple[SetupModel, list[Cmd]]:
    """Give ``message`` to the selected form (not while a check or a save runs)."""
    if model.phase != "idle":
        return model, []
    form = model.forms[model.name]
    updated, cmds = form.update(message)
    if updated is form and not cmds:
        return model, []
    if model.name in model.failed and updated.value != form.value:
        # The values changed: the failed check no longer says anything about them.
        updated = reset(updated.with_error(""))
        model = replace(model, failed=_without(model.failed, model.name))
    return replace(model, forms={**model.forms, model.name: updated}), list(cmds)


def _next(run: SetupRun, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    if model.focus == "profile":
        return _leave_profile(run, model, "list")
    if model.focus == "list":
        return replace(model, focus=_opened_focus(model)), []
    if model.focus == "section":
        return model, []
    return _in_form(model, NextField())


def _previous(run: SetupRun, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    if model.focus == "form":
        moved, cmds = _in_form(model, PrevField())
        if moved is model and not cmds:
            return replace(model, focus=_closed_focus(model)), []  # at its first field
        return moved, cmds
    if model.focus == "section":
        return replace(model, focus="list"), []
    if model.focus == "list":
        return replace(model, focus="profile"), []
    return _leave_profile(run, model, "profile")


def _activate(run: SetupRun, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    if model.focus == "profile":
        return _load(run, model)
    if model.focus == "list":
        return replace(model, focus=_opened_focus(model)), []
    if model.focus == "section":
        return _open(run, model)
    return _in_form(model, Activate())


def _submit(run: SetupRun, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    if model.focus == "profile":
        # A typed profile is loaded first (as on enter); saving is a second ctrl-s, once
        # the rows are the new profile's.
        return _leave_profile(run, model, "profile")
    if model.focus == "section":
        pane = model.pane
        return _write(run, model) if pane and pane.editing is not None else (model, [])
    if model.name != INSTALL_ROW and model.focus == "list":
        return model, []  # a section is written a field at a time, from its rows
    return _in_form(replace(model, focus="form"), Submit())


def _back(model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    if model.focus == "form":
        return replace(model, focus=_closed_focus(model)), []
    if model.focus == "section":
        pane = model.pane
        if pane is not None and (pane.editing is not None or pane.confirm):
            return _with_pane(model, model.name, pane.closed()), []
        return replace(model, focus="list"), []
    return _leave(model, "quit")


def _leave(model: SetupModel, how: Literal["quit", "interrupt"]) -> tuple[SetupModel, list[Cmd]]:
    """End the screen with the result, once a running write has finished."""
    if model.phase in ("saving", "registering", "writing"):
        return replace(model, leaving=how), []
    return model, [Cmd.send(Quit(_result(model, interrupted=how == "interrupt")))]


def _leave_profile(run: SetupRun, model: SetupModel, then: Focus) -> tuple[SetupModel, list[Cmd]]:
    """The profile field loses the keyboard: what is typed there is loaded, as on enter.

    The field and the rows never disagree: a name that differs from ``current`` loads (and
    focuses the list once loaded); the same name just moves the focus to ``then``.
    """
    loaded, cmds = _load(run, model)
    if cmds or loaded is not model:
        return loaded, cmds  # loading, or the name was refused
    return replace(model, focus=then), []


def _load(run: SetupRun, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    name = model.profile.value.strip()
    if not name:
        return replace(model, profile=model.profile.with_error("Enter a profile name.")), []
    if name == model.current or model.phase != "idle":
        return model, []
    services = run.services

    def load_profile() -> object:
        return ProfileLoaded(name, read_listing(name, services))

    return replace(model, phase="loading"), [Cmd(load_profile, name="load_profile")]


def _loaded(
    run: SetupRun, model: SetupModel, profile: str, listing: Listing
) -> tuple[SetupModel, list[Cmd]]:
    if model.phase != "loading":
        return model, []
    rows = tuple(CapRow(name, row_status(name, listing)) for name in run.order)
    return (
        replace(
            model,
            profile=_profile_field(model.profile, listing),
            current=profile,
            rows=(model.rows[0], *rows),
            states=listing.states,
            forms={
                INSTALL_ROW: model.forms[INSTALL_ROW],
                **_forms(run, listing.states, profile),
            },
            opened=listing.states,
            panes=_panes(run, listing, profile),
            phase="idle",
            pending=None,
            failed={},
            focus="list",
        ),
        [],
    )
