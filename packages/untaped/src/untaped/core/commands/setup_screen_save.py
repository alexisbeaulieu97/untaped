"""The setup screen's saves: check a connection, write it, and register this install.

Saving a connection checks before it writes: the form's values become a
:class:`~untaped.core.commands.setup_write.Candidate`, a Command token source
runs first with the real terminal, the plugin's online checks run against the
values inside a settings overlay, and only a pass (or ``Save anyway``) runs
the write, as a write command. Registering this install writes its reference
in the background. A quit asked for while a write runs ends the screen once
it finished (:func:`_after`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from rich.console import RenderableType
from rich.text import Text

from untaped.core.commands.register import Register
from untaped.core.commands.setup_connection import (
    anyway_buttons,
    candidate,
    connection_form,
    reset,
    with_buttons,
)
from untaped.core.commands.setup_screen_listing import _relisted
from untaped.core.commands.setup_screen_model import (
    INSTALL_ROW,
    NotSaved,
    Primed,
    Probed,
    Registered,
    Saved,
    SetupModel,
    SetupRun,
    _closed_focus,
    _error_text,
    _index,
    _on_buttons,
    _registrable,
    _result,
    _status,
    _with_pane,
    _without,
)
from untaped.core.commands.setup_sections import Listing, read_listing
from untaped.core.commands.setup_write import Candidate, landed, overlay_values, write_candidate
from untaped.core.config.use_cases import Note
from untaped.core.doctor_rows import online_probe_rows
from untaped.screen.components.buttons import Button, Buttons
from untaped.screen.components.draw import role_style
from untaped.screen.components.form import Form
from untaped.screen.core import Cmd, Frame, Quit, Submit
from untaped.sdk.errors import ConfigError, ErrorCategory
from untaped.sdk.network.auth import CommandToken, forget_token_command
from untaped.sdk.words import command_line, shown_path

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin


def _start(
    run: SetupRun, model: SetupModel, values: Mapping[str, object]
) -> tuple[SetupModel, list[Cmd]]:
    """The form passed its own validation: check the values before anything is written."""
    if model.phase != "idle":
        return model, []  # one check at a time: a save while one runs is ignored
    name = model.name
    plugin = run.services[name]
    del values  # the form itself holds them, each in its shape's field
    try:
        asked = candidate(plugin, model.current, model.forms[name], model.opened[name])
    except ConfigError as exc:
        field = exc.details.get("field")
        if isinstance(field, str):
            return _refuse(model, name, field, str(exc)), []
        return _fail(model, name, str(exc)), []

    def prime() -> object:
        # Runs with the real terminal; each secret is cached for the probe.
        for command in asked.commands:
            argv = list(command)
            forget_token_command(argv)  # every check runs the command, a retry included
            try:
                CommandToken(argv, setting=f"the {plugin.name} token command").get_secret_value()
            except ConfigError as exc:
                return Probed(name, (("fail", str(exc)),))
        return Primed(name)

    checking = replace(
        model,
        phase="probing",
        pending=asked,
        failed=_without(model.failed, name),
        rows=_status(model.rows, name, "checking"),
        forms={**model.forms, name: reset(model.forms[name].with_error(""))},
    )
    if asked.commands:
        return checking, [Cmd(prime, suspend=True, name="prime")]
    return checking, [_probe(run, asked)]


def _primed(run: SetupRun, model: SetupModel, name: str) -> tuple[SetupModel, list[Cmd]]:
    asked = model.pending
    if model.phase != "probing" or asked is None or asked.name != name:
        return model, []
    return model, [_probe(run, asked)]


def _probe(run: SetupRun, asked: Candidate) -> Cmd:
    """The background check of ``asked``: the plugin's online checks over its values."""
    overlay = overlay_values(asked)
    result, name, profile = run.result, asked.name, asked.profile

    def probe() -> object:
        rows = online_probe_rows(result, profile, name, overlay)
        return Probed(name, tuple((str(row["status"]), str(row["detail"])) for row in rows))

    return Cmd(probe, name="probe")


def _probed(
    run: SetupRun, model: SetupModel, name: str, rows: tuple[tuple[str, str], ...]
) -> tuple[SetupModel, list[Cmd]]:
    asked = model.pending
    if model.phase != "probing" or asked is None or asked.name != name:
        return model, []
    failing = [detail for status, detail in rows if status == "fail"]
    if not failing:
        return _save(run, model, replace(asked, checked="passed" if rows else "unchecked"))
    form = model.forms[name].with_error(failing[0] or "the check failed")
    form = _on_buttons(with_buttons(form, anyway_buttons()))
    failed = replace(asked, checked="failed")
    return (
        replace(
            model,
            phase="idle",
            pending=failed,
            failed={**model.failed, name: failed},
            rows=_status(model.rows, name, "failed"),
            forms={**model.forms, name: form},
            selected=_index(model.rows, name),
            focus="form",
        ),
        [],
    )


def _pressed(run: SetupRun, model: SetupModel, button: str) -> tuple[SetupModel, list[Cmd]]:
    if button == "register":
        return register_install(model)
    if button == "save":
        return model, [Cmd.send(Submit())]
    if button == "save_anyway":
        asked = model.failed.get(model.name)
        if model.phase != "idle" or asked is None:
            return model, []
        return _save(run, model, asked)
    # Cancel: forget the failed check and go back to the section's rows.
    form = reset(model.forms[model.name].with_error(""))
    return replace(
        model,
        forms={**model.forms, model.name: form},
        failed=_without(model.failed, model.name),
        focus=_closed_focus(model),
    ), []


def _save(run: SetupRun, model: SetupModel, asked: Candidate) -> tuple[SetupModel, list[Cmd]]:
    name = asked.name
    plugin, preflight, services = run.services[name], run.preflight, run.services

    def save() -> object:
        try:
            notes = write_candidate(plugin, asked, preflight)
        except Exception as exc:  # shown on the form, as any failed command is
            conflict = isinstance(exc, ConfigError) and exc.category is ErrorCategory.CONFLICT
            listing = _listing(asked.profile, services)
            now = None if listing is None else listing.states.get(name)
            written = () if now is None else landed(asked, now)
            return NotSaved(name, _error_text(exc), conflict, listing, written)
        return Saved(name, asked.profile, tuple(notes), _listing(asked.profile, services))

    return (
        replace(
            model,
            phase="saving",
            pending=asked,
            failed=_without(model.failed, name),
            rows=_status(model.rows, name, "saving"),
        ),
        [Cmd(save, write=True, name="save")],
    )


def _saved(run: SetupRun, model: SetupModel, message: Saved) -> tuple[SetupModel, list[Cmd]]:
    asked = model.pending
    if model.phase != "saving" or asked is None or asked.name != message.name:
        return model, []
    name = message.name
    status = {"passed": "ok", "unchecked": "saved", "failed": "saved, check failed"}[asked.checked]
    done = replace(model, phase="idle", rows=_status(model.rows, name, status))
    if message.listing is not None:
        done = _relisted(run, done, message.listing)
    state = done.states[name]
    done = replace(
        done,
        pending=None,
        failed=_without(model.failed, name),
        forms={
            **done.forms,
            name: connection_form(run.services[name], state, run.store),
        },
        opened={**done.opened, name: state},
        saved=(*model.saved, (message.profile, name)),
        notes=(*model.notes, *message.notes),
        focus="section",
    )
    return _after(done)


def _not_saved(run: SetupRun, model: SetupModel, message: NotSaved) -> tuple[SetupModel, list[Cmd]]:
    """A save failed: the form keeps what the user typed, now compared with the profile.

    A conflict wrote nothing: the form opens again on what the profile
    holds now. Otherwise the settings the save wrote before failing are
    what the form opened on now, so a retry neither refuses them as
    changed nor writes them twice; any other still expects what it did.
    """
    asked = model.pending
    if model.phase != "saving" or asked is None or asked.name != message.name:
        return model, []
    name = message.name
    done = model if message.listing is None else _relisted(run, model, message.listing)
    fresh = done.states.get(name)
    if fresh is not None and message.conflict:
        form = connection_form(run.services[name], fresh, run.store)
        opened = fresh
        done = replace(done, forms={**done.forms, name: form})
    elif fresh is not None:
        keys = message.written
        opened = model.opened[name]
        values = {**opened.values, **{key: fresh.values.get(key) for key in keys}}
        own = {**opened.own, **{key: fresh.own.get(key) for key in keys}}
        opened = replace(opened, values=values, own=own)
    else:
        opened = model.opened[name]
    done = replace(done, opened={**done.opened, name: opened})
    return _save_failed(done, asked, message.error)


def _failed(model: SetupModel, error: BaseException) -> tuple[SetupModel, list[Cmd]]:
    text = _error_text(error)
    if model.phase == "registering":
        form = model.forms[INSTALL_ROW].with_error(f"could not register this install: {text}")
        refused: Note = ("warning", f"could not register this install: {text}")
        failed = replace(
            model,
            phase="idle",
            rows=_status(model.rows, INSTALL_ROW, "not registered"),
            forms={**model.forms, INSTALL_ROW: form},
            notes=(*model.notes, refused),  # printed at exit, wherever the cursor went
        )
        return _after(failed)
    if model.phase == "loading":
        return replace(model, phase="idle", profile=model.profile.with_error(text)), []
    if model.phase in ("opening", "writing"):
        pane = model.pane
        stopped = replace(model, phase="idle")
        if pane is not None:
            stopped = _with_pane(stopped, model.name, pane.with_error(text))
        return _after(stopped)
    asked = model.pending
    if model.phase not in ("probing", "saving") or asked is None:
        return model, []
    return _save_failed(model, asked, text)


def _save_failed(model: SetupModel, asked: Candidate, text: str) -> tuple[SetupModel, list[Cmd]]:
    """The check or the save of ``asked`` failed with ``text``: show it on its form."""
    wrote = model.phase == "saving"
    failed = replace(_fail(model, asked.name, text), phase="idle", pending=None)
    if wrote and model.leaving is not None:
        note: Note = ("warning", f"could not save {asked.name}: {text}")
        failed = replace(failed, notes=(*failed.notes, note))
    return _after(failed)


def _refuse(model: SetupModel, name: str, key: str, text: str) -> SetupModel:
    """The form refused the value of its field ``key``: show why there, and focus it."""
    failed = _fail(model, name, "")
    form = failed.forms[name]
    slot = next(index for index, (each, _) in enumerate(form.fields) if each == key)
    fields = tuple(
        (each, field.with_error(text) if index == slot else field)
        for index, (each, field) in enumerate(form.fields)
    )
    refused = replace(form, fields=fields, focus=slot)
    return replace(failed, forms={**failed.forms, name: refused})


def _fail(model: SetupModel, name: str, text: str) -> SetupModel:
    form = _on_buttons(reset(model.forms[name].with_error(text)))
    return replace(
        model,
        failed=_without(model.failed, name),
        rows=_status(model.rows, name, "failed"),
        forms={**model.forms, name: form},
        selected=_index(model.rows, name),
        focus="form",
    )


def _after(model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    """A write is over: end the screen now if a quit was waiting for it."""
    if model.leaving is None:
        return model, []
    return model, [Cmd.send(Quit(_result(model, interrupted=model.leaving == "interrupt")))]


# --- this install ---------------------------------------------------------


def register_install(model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    """``Register`` pressed on this install's row: write its reference in the background."""
    if not _registrable(model):
        return model, []
    registering = replace(
        model,
        phase="registering",
        rows=_status(model.rows, INSTALL_ROW, "registering"),
        forms={**model.forms, INSTALL_ROW: model.forms[INSTALL_ROW].with_error("")},
    )
    return registering, [Cmd(_installed, write=True, name="register")]


def _registered(model: SetupModel, text: str) -> tuple[SetupModel, list[Cmd]]:
    if model.phase != "registering":
        return model, []
    note: Note = ("success", text)
    done = replace(
        model,
        phase="idle",
        rows=_status(model.rows, INSTALL_ROW, "registered"),
        forms={**model.forms, INSTALL_ROW: _install_form()},
        notes=(*model.notes, note),
        registered=True,
        focus="list",
    )
    return _after(done)


def _installed() -> object:
    """Register this install, in the background; what it wrote."""
    from untaped.sdk.storage._refs import (  # noqa: PLC0415 - off the startup path
        register_this_install,
    )

    ref, _changed = register_this_install()
    held = ", ".join(target.describe() for target in ref.targets) or "no stores"
    return Registered(f"registered this install, {ref.describe()} ({held})")


def _install_form() -> Form:
    """This install's form: what registering does, and ``Register`` while it is not."""
    from untaped.sdk.storage._refs import environment, install_state, this_install  # noqa: PLC0415

    state, ref, env = install_state(), this_install(), environment()
    if state == "source tree" or env is None:
        text = "This untaped runs from a source tree: only an installed untaped can be registered."
        return Form((("about", _Note(text)),))
    if state == "registered" and ref is not None:
        text = (
            f"Registered: {ref.describe()}. Its runs remove the store layouts no registered "
            f"install uses. `{command_line(Register.spelled('--remove'))}` takes it back."
        )
        return Form((("about", _Note(text)),))
    text = (
        f"Register {shown_path(env)} so its runs remove the store layouts no registered "
        f"install uses, and keep the ones it does (`{command_line(Register.spelled())}`)."
    )
    buttons = Buttons((Button("register", "Register", "primary"),))
    return Form((("about", _Note(text)), ("buttons", buttons)), focus=1)


@dataclass(frozen=True)
class _Note:
    """A form's line of muted text: no value, never focused for input, never in error."""

    text: str
    error: str = ""

    @property
    def value(self) -> str:
        return ""

    def update(self, message: object) -> tuple[_Note, list[Cmd]]:
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        return Text(self.text, style=role_style(frame, "screen.muted"))

    def with_error(self, text: str) -> _Note:
        return self

    def validate(self) -> str:
        return ""


def _listing(profile: str, services: Mapping[str, ComposedPlugin]) -> Listing | None:
    """What ``profile`` shows now, or ``None`` when the config cannot be read."""
    try:
        return read_listing(profile, services)
    except ConfigError:
        return None
