"""The setup screen's section rows: open a row's field, write it, unset it.

A row's field is read and written as ``config get`` and ``config set`` do
(:func:`~untaped.core.commands.setup_sections.open_command`,
:func:`~untaped.core.commands.setup_sections.write_command`); the listing is
read again after each write.
"""

from __future__ import annotations

from dataclasses import replace

from untaped.core.commands.setup_screen_listing import _relisted
from untaped.core.commands.setup_screen_model import SetupModel, SetupRun, _with_pane
from untaped.core.commands.setup_screen_save import _after
from untaped.core.commands.setup_sections import (
    FieldOpened,
    UnsetAsked,
    Written,
    field_for,
    open_command,
    write_command,
)
from untaped.core.commands.setup_write import RankTarget
from untaped.core.config.use_cases import Note
from untaped.screen.core import Cmd


def _row_answer(
    run: SetupRun, model: SetupModel, message: FieldOpened | UnsetAsked | Written
) -> tuple[SetupModel, list[Cmd]]:
    """A row's field was read, its unset confirmed, or its write finished."""
    match message:
        case FieldOpened():
            return _opened(model, message)
        case UnsetAsked(key=key, ranking=ranking):
            return _unset(run, model, key, ranking)
        case Written():
            return _written(run, model, message)


def _open(run: SetupRun, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    """Enter on a row: write its open field, open the connection's form, or read its field."""
    pane = model.pane
    if pane is None or model.phase != "idle" or pane.confirm:
        return model, []
    if pane.editing is not None:
        return _write(run, model)
    row = pane.row
    if row is None:
        return model, []
    if not row.key:
        return replace(model, focus="form"), []  # the connection row
    if row.descriptor is None:
        return model, []  # read-only
    return replace(model, phase="opening"), [open_command(model.current, row)]


def _opened(model: SetupModel, message: FieldOpened) -> tuple[SetupModel, list[Cmd]]:
    if model.phase != "opening":
        return model, []
    section = _section(message.key)
    pane = model.panes.get(section)
    opened = model if pane is None else _with_pane(model, section, field_for(pane, message))
    return replace(opened, phase="idle"), []


def _write(run: SetupRun, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
    """Write the open field, as ``config set`` would; its own check first."""
    pane = model.pane
    row = None if pane is None else pane.row
    if pane is None or row is None or model.phase != "idle":
        return model, []
    refused = pane.checked()
    if refused is not None:
        return _with_pane(model, model.name, refused), []
    value = pane.to_write()
    if value == pane.initial:
        # Untouched: writing it would pin an inherited or default value here.
        return _with_pane(model, model.name, pane.closed()), []
    cmd = write_command(
        model.current,
        row.key,
        value,
        pane.expected,
        run.services,
        ranking=row.ranking,
        owners=run.owners,
    )
    return replace(model, phase="writing"), [cmd]


def _unset(
    run: SetupRun, model: SetupModel, key: str, ranking: RankTarget | None
) -> tuple[SetupModel, list[Cmd]]:
    if model.phase != "idle":
        return model, []
    cmd = write_command(
        model.current, key, None, None, run.services, ranking=ranking, owners=run.owners
    )
    return replace(model, phase="writing"), [cmd]


def _written(run: SetupRun, model: SetupModel, message: Written) -> tuple[SetupModel, list[Cmd]]:
    if model.phase != "writing":
        return model, []
    section = _section(message.key)
    done = replace(model, phase="idle")
    if message.listing is not None:
        done = _relisted(run, done, message.listing)
    pane = done.panes.get(section)
    if message.error:
        if pane is not None:
            if pane.editing is not None and message.listing is None:
                pane = replace(pane, editing=pane.editing.with_error(message.error))
            else:
                pane = pane.closed().with_error(message.error)
            done = _with_pane(done, section, pane)
        if done.leaving is not None:
            note: Note = ("warning", f"could not write {message.key}: {message.error}")
            done = replace(done, notes=(*done.notes, note))
        return _after(done)
    if pane is not None:
        done = _with_pane(done, section, pane.closed())
    if message.changed:
        written = (*done.written, (model.current, message.key))
        done = replace(done, written=written, notes=(*done.notes, *message.notes))
    return _after(done)


def _section(key: str) -> str:
    """The section a key belongs to: its first segment."""
    return key.partition(".")[0]
