"""The ``untaped setup`` screen: its model, update and view.

Two panes under a profile field: the capabilities setup configures (those
whose profile model has ``base_url`` and ``token``) with a status each, and the
selected one's form (base URL, a token source as tabs, buttons). Saving checks
before it writes: the form's values become a :class:`Candidate`, the
capability's online checks run against them inside a settings overlay (nothing
is stored yet, :func:`untaped.management.doctor.online_probe_rows`), and only a
pass runs the write, as a write command, so quitting never leaves it half done.
A failed check shows its reason under the form and offers ``Save anyway``, for
that capability until its form changes or saves (each capability keeps its own).
A Command token source is run first, on every check, as a suspend command, so a
``pass`` or ``op`` prompt gets the real terminal; its token then sits in the
process cache for the background check. Leaving the profile field loads the
profile typed there, so the field and the forms never disagree.

What a save has to say (a profile created, a token replaced or to export)
comes back as notes, which the command prints after the screen closes with the doctor rows of what was
configured (:class:`SetupResult`). Esc and ctrl-c on the list end the screen
with that result (ctrl-c flagged ``interrupted``, which ``setup`` turns into
exit 130); neither is a cancel. A quit while a save runs waits for it, so the
result always says what was written.

The typed token lives in a ``SecretInput`` and a ``Candidate`` as a ``SecretStr``
and in no frame, repr or message of this module's own. The model never holds the
plain text of a token the profile already stores (``ServiceState.plaintext`` is
left out of its repr).
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from typing import Any, Literal

from pydantic import SecretStr
from rich.console import Group, RenderableType
from rich.text import Text

from untaped.auth import CommandToken, forget_token_command, takes_token_command, token_env_names
from untaped.capabilities.registry import CapabilitySpec, CompositionResult
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError
from untaped.management.doctor import online_probe_rows
from untaped.management.setup_plan import SETUP_ALTERNATIVE, SETUP_COMMAND
from untaped.management.setup_state import ServiceState, service_states
from untaped.management.setup_write import Candidate, Note, overlay_values, write_candidate
from untaped.messages import hint
from untaped.profile_resolver import DEFAULT_PROFILE
from untaped.prompts import PromptChoice
from untaped.screen.components.buttons import Button, Buttons, Pressed
from untaped.screen.components.choices import ListItem, SingleList
from untaped.screen.components.draw import role_style
from untaped.screen.components.form import Form, Submitted
from untaped.screen.components.inputs import SecretInput, TextInput
from untaped.screen.components.layout import Panes
from untaped.screen.components.tabs import Tab, Tabs
from untaped.screen.core import (
    Activate,
    Back,
    Binding,
    Cmd,
    CmdError,
    Frame,
    Interrupt,
    Key,
    NextField,
    Paste,
    PrevField,
    Quit,
    Screen,
    Submit,
)
from untaped.settings import resolve_config_path
from untaped.token_store import TokenStore, preset_entry

__all__ = ["SetupModel", "SetupResult", "setup_screen"]

type Focus = Literal["profile", "list", "form"]
type Phase = Literal["idle", "loading", "probing", "saving"]

_TAB_LABELS = {
    "keep": "Keep",
    "move": "Move",
    "command": "Command",
    "env": "Env",
    "enter": "Enter",
}

#: The role each status is drawn in; ``checking`` and ``saving`` take the ellipsis token.
_STATUS_ROLES = {
    "configured": "screen.value",
    "missing token": "screen.accent",
    "not configured": "screen.muted",
    "invalid settings": "screen.error",
    "checking": "screen.accent",
    "saving": "screen.accent",
    "ok": "screen.success",
    "saved": "screen.success",
    "saved, check failed": "screen.error",
    "failed": "screen.error",
}
_PROGRESS = ("checking", "saving")


@dataclass(frozen=True)
class SetupResult:
    """What the screen did: for ``profile``, the capabilities written and the lines to print.

    ``notes`` are what the saves had to say, printed after the screen closes.
    ``interrupted`` is ctrl-c, which ``setup`` reports as exit 130 after printing
    the rest.
    """

    profile: str
    touched: tuple[str, ...]
    notes: tuple[Note, ...]
    interrupted: bool = False


@dataclass(frozen=True)
class CapRow:
    """One capability on the left: its name and status (a key of the status table)."""

    name: str
    status: str


@dataclass(frozen=True)
class Probed:
    """The check of ``name`` finished: the ``(status, detail)`` of each of its online checks."""

    name: str
    rows: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Primed:
    """The suspended token command of ``name`` ran and printed a token."""

    name: str


@dataclass(frozen=True)
class Saved:
    """The write for ``name`` finished; ``state`` is what the profile resolves to now."""

    name: str
    profile: str
    notes: tuple[Note, ...]
    state: ServiceState | None


@dataclass(frozen=True)
class ProfileLoaded:
    """The states of every capability in ``profile``, read for the profile field."""

    profile: str
    states: Mapping[str, ServiceState]


@dataclass(frozen=True)
class SetupModel:
    """Everything the screen shows and every decision it holds."""

    profile: TextInput
    """The profile field (what is typed; ``current`` is the profile the forms are for)."""
    current: str
    rows: tuple[CapRow, ...]
    states: Mapping[str, ServiceState]
    forms: Mapping[str, Form]
    config_path: str = ""
    selected: int = 0
    focus: Focus = "list"
    phase: Phase = "idle"
    pending: Candidate | None = None
    """The candidate being checked or saved, or the one whose check failed."""
    failed: Mapping[str, Candidate] = field(default_factory=dict)
    """Per capability, the candidate whose check failed: its form offers ``Save anyway``."""
    leaving: Literal["quit", "interrupt"] | None = None
    """A quit asked for while a save runs: it ends the screen once the write finished."""
    saved: tuple[tuple[str, str], ...] = ()
    """``(profile, capability)`` for every write that finished."""
    notes: tuple[Note, ...] = ()

    @property
    def name(self) -> str:
        """The selected capability."""
        return self.rows[self.selected].name


def setup_screen(
    result: CompositionResult,
    services: Mapping[str, CapabilitySpec],
    *,
    profile: str,
    store: TokenStore | None,
    states: Mapping[str, ServiceState],
    profiles: Sequence[str] = (),
) -> Screen[SetupModel, SetupResult]:
    """The setup screen for ``services`` in ``profile``.

    ``states`` is each service's state in ``profile``, read before the screen
    opens; ``store`` the token store picked once for the run (``None`` when none
    works or no service takes a command); ``profiles`` the names the profile
    field completes.
    """
    app = _Setup(result, dict(services), store, tuple(profiles), profile, states)
    return Screen(
        init=app.init,
        update=app.update,
        view=app.view,
        title="Set up untaped",
        command=SETUP_COMMAND,
        alternative=SETUP_ALTERNATIVE,
        keys=(
            Binding("up", "previous", None, when=lambda model: model.focus == "list"),
            Binding("down", "next", None, when=lambda model: model.focus == "list"),
            Binding("left", "previous choice", None, when=lambda model: model.focus == "form"),
            Binding("right", "next choice", None, when=lambda model: model.focus == "form"),
        ),
        shared_labels=_SHARED_LABELS,
        layout="full",
    )


def _tab_label(model: SetupModel) -> str | None:
    """Tab walks the profile field, the list and the form; in the form it is the default."""
    return {"profile": "list", "list": "form", "form": None}[model.focus]


def _enter_label(model: SetupModel) -> str | None:
    """Enter loads the profile or opens a form; in a form it is the field's (or button's) own."""
    return {"profile": "load", "list": "open", "form": None}[model.focus]


def _save_label(model: SetupModel) -> str | None:
    """Ctrl-s saves the form (checking first); in the profile field it loads, so not offered."""
    return None if model.focus == "profile" else "save"


#: What the shared keys do here, for the footer and the help overlay.
_SHARED_LABELS: Mapping[str, str | Callable[[SetupModel], str | None]] = {
    "tab": _tab_label,
    "enter": _enter_label,
    "ctrl-s": _save_label,
}


class _Setup:
    """The screen's functions; the pieces of the run they close over."""

    def __init__(
        self,
        result: CompositionResult,
        services: dict[str, CapabilitySpec],
        store: TokenStore | None,
        profiles: tuple[str, ...],
        profile: str,
        states: Mapping[str, ServiceState],
    ) -> None:
        self.result = result
        self.services = services
        self.store = store
        self.profiles = profiles
        self.profile = profile
        self.states = states
        # Tested once, just before the first token goes to the store.
        self.preflight = cache(store.preflight) if store is not None else _no_preflight

    # --- init and view --------------------------------------------------------

    def init(self) -> tuple[SetupModel, list[Cmd]]:
        field = TextInput(
            "Profile",
            self.profile,
            complete=lambda text: [name for name in self.profiles if name.startswith(text)],
        )
        model = SetupModel(
            profile=field,
            current=self.profile,
            rows=self._rows(self.states),
            states=self.states,
            forms=self._forms(self.states, self.profile),
            config_path=str(resolve_config_path()),
        )
        return model, []

    def view(self, model: SetupModel, frame: Frame) -> RenderableType:
        muted = role_style(frame, "screen.muted")
        header = Text()
        header.append(" untaped setup ", style=role_style(frame, "screen.highlight"))
        header.append(f"  config {model.config_path}", style=muted)
        field_width = min(frame.width, 40)
        field = model.profile.view(frame, focused=model.focus == "profile", width=field_width)
        used = 1 + _field_rows(frame, model.profile)
        inner = replace(frame, height=max(1, frame.height - used))
        left = SingleList(
            "",
            tuple(self._item(frame, row) for row in model.rows),
            value=model.name,
            cursor=model.selected,
        )
        panes = Panes(
            left,
            model.forms[model.name],
            focus=0 if model.focus == "list" else 1,
            left_title="Capabilities",
            right_title=model.name,
        )
        return Group(header, field, panes.view(inner, focused=model.focus != "profile"))

    def _item(self, frame: Frame, row: CapRow) -> ListItem:
        label = row.status + frame.ellipsis() if row.status in _PROGRESS else row.status
        return ListItem(row.name, row.name, detail=label, detail_role=_STATUS_ROLES[row.status])

    # --- messages -------------------------------------------------------------

    def update(self, model: SetupModel, message: object) -> tuple[SetupModel, list[Cmd]]:
        if isinstance(message, Key | Paste):
            return self._edit(model, message)
        if isinstance(message, Activate | Back | Interrupt | NextField | PrevField | Submit):
            return self._shared(model, message)
        return self._answer(model, message)

    def _shared(
        self,
        model: SetupModel,
        message: Activate | Back | Interrupt | NextField | PrevField | Submit,
    ) -> tuple[SetupModel, list[Cmd]]:
        """A shared key the focused field passed on."""
        match message:
            case NextField():
                return self._next(model)
            case PrevField():
                return self._previous(model)
            case Activate():
                return self._activate(model)
            case Submit():
                return self._submit(model)
            case Back():
                return self._back(model)
            case Interrupt():
                return self._leave(model, "interrupt")

    def _answer(self, model: SetupModel, message: object) -> tuple[SetupModel, list[Cmd]]:
        """The form's submit, a button, or what a command brought back."""
        match message:
            case Submitted(values=values):
                return self._start(model, values)
            case Pressed(id=button):
                return self._pressed(model, button)
            case Primed(name=name):
                return self._primed(model, name)
            case Probed(name=name, rows=rows):
                return self._probed(model, name, rows)
            case Saved():
                return self._saved(model, message)
            case ProfileLoaded(profile=profile, states=states):
                return self._loaded(model, profile, states)
            case CmdError(error=error):
                return self._failed(model, error)
        return model, []

    # --- keys -----------------------------------------------------------------

    def _edit(self, model: SetupModel, message: Key | Paste) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "profile":
            if model.phase != "idle":
                return model, []  # the field and the forms must agree: no typing mid-load or save
            field, cmds = model.profile.update(message)
            return (model if field is model.profile else replace(model, profile=field)), cmds
        if model.focus == "list":
            if not isinstance(message, Key):
                return model, []
            items = tuple(ListItem(row.name, row.name) for row in model.rows)
            moved, _ = SingleList("", items, value=model.name, cursor=model.selected).update(
                message
            )
            if moved.cursor == model.selected:
                return model, []
            return replace(model, selected=moved.cursor or 0), []
        return self._in_form(model, message)

    def _in_form(self, model: SetupModel, message: object) -> tuple[SetupModel, list[Cmd]]:
        """Give ``message`` to the selected form (not while a check or a save runs)."""
        if model.phase != "idle":
            return model, []
        form = model.forms[model.name]
        updated, cmds = form.update(message)
        if updated is form and not cmds:
            return model, []
        if model.name in model.failed and updated.value != form.value:
            # The values changed: the failed check no longer says anything about them.
            updated = _reset(updated.with_error(""))
            model = replace(model, failed=_without(model.failed, model.name))
        return replace(model, forms={**model.forms, model.name: updated}), list(cmds)

    def _next(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "profile":
            return self._leave_profile(model, "list")
        if model.focus == "list":
            return replace(model, focus="form"), []
        return self._in_form(model, NextField())

    def _previous(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "form":
            moved, cmds = self._in_form(model, PrevField())
            if moved is model and not cmds:
                return replace(model, focus="list"), []  # the form was at its first field
            return moved, cmds
        if model.focus == "list":
            return replace(model, focus="profile"), []
        return self._leave_profile(model, "profile")

    def _activate(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "profile":
            return self._load(model)
        if model.focus == "list":
            return replace(model, focus="form"), []
        return self._in_form(model, Activate())

    def _submit(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "profile":
            # A typed profile is loaded first (as on enter); saving is a second ctrl-s, once
            # the forms are the new profile's.
            return self._leave_profile(model, "profile")
        return self._in_form(replace(model, focus="form"), Submit())

    def _back(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "form":
            return replace(model, focus="list"), []
        return self._leave(model, "quit")

    def _leave(
        self, model: SetupModel, how: Literal["quit", "interrupt"]
    ) -> tuple[SetupModel, list[Cmd]]:
        """End the screen with the result, once a running save has finished."""
        if model.phase == "saving":
            return replace(model, leaving=how), []
        return model, [Cmd.send(Quit(_result(model, interrupted=how == "interrupt")))]

    # --- profile --------------------------------------------------------------

    def _leave_profile(self, model: SetupModel, then: Focus) -> tuple[SetupModel, list[Cmd]]:
        """The profile field loses the keyboard: what is typed there is loaded, as on enter.

        The field and the forms never disagree: a name that differs from ``current`` loads (and
        focuses the list once loaded); the same name just moves the focus to ``then``.
        """
        loaded, cmds = self._load(model)
        if cmds or loaded is not model:
            return loaded, cmds  # loading, or the name was refused
        return replace(model, focus=then), []

    def _load(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        name = model.profile.value.strip()
        if not name:
            return replace(model, profile=model.profile.with_error("Enter a profile name.")), []
        if name == model.current or model.phase != "idle":
            return model, []
        services = self.services

        def load_profile() -> object:
            return ProfileLoaded(name, service_states(services, name, read_config_dict()))

        return replace(model, phase="loading"), [Cmd(load_profile, name="load_profile")]

    def _loaded(
        self, model: SetupModel, profile: str, states: Mapping[str, ServiceState]
    ) -> tuple[SetupModel, list[Cmd]]:
        if model.phase != "loading":
            return model, []
        return (
            replace(
                model,
                current=profile,
                rows=self._rows(states),
                states=states,
                forms=self._forms(states, profile),
                phase="idle",
                pending=None,
                failed={},
                focus="list",
            ),
            [],
        )

    # --- save -----------------------------------------------------------------

    def _start(
        self, model: SetupModel, values: Mapping[str, object]
    ) -> tuple[SetupModel, list[Cmd]]:
        """The form passed its own validation: check the values before anything is written."""
        if model.phase != "idle":
            return model, []  # one check at a time: a save while one runs is ignored
        name = model.name
        spec = self.services[name]
        try:
            candidate = _candidate(spec, model.current, values)
        except ConfigError as exc:
            return self._fail(model, name, str(exc)), []

        def prime() -> object:
            # Runs with the real terminal; the token is cached for the probe.
            section = spec.config_section
            argv = list(candidate.argv or ())
            forget_token_command(argv)  # every check runs the command, a retry included
            try:
                CommandToken(argv, section=section).get_secret_value()
            except ConfigError as exc:
                return Probed(name, (("fail", str(exc)),))
            return Primed(name)

        checking = replace(
            model,
            phase="probing",
            pending=candidate,
            failed=_without(model.failed, name),
            rows=_status(model.rows, name, "checking"),
            forms={**model.forms, name: _reset(model.forms[name].with_error(""))},
        )
        if candidate.how == "command":
            return checking, [Cmd(prime, suspend=True, name="prime")]
        return checking, [self._probe(candidate, model.states[name])]

    def _primed(self, model: SetupModel, name: str) -> tuple[SetupModel, list[Cmd]]:
        candidate = model.pending
        if model.phase != "probing" or candidate is None or candidate.name != name:
            return model, []
        return model, [self._probe(candidate, model.states[name])]

    def _probe(self, candidate: Candidate, state: ServiceState) -> Cmd:
        """The background check of ``candidate``: the capability's online checks over its values."""
        overlay = overlay_values(candidate, state)
        result, name, profile = self.result, candidate.name, candidate.profile

        def probe() -> object:
            rows = online_probe_rows(result, profile, name, overlay)
            return Probed(name, tuple((str(row["status"]), str(row["detail"])) for row in rows))

        return Cmd(probe, name="probe")

    def _probed(
        self, model: SetupModel, name: str, rows: tuple[tuple[str, str], ...]
    ) -> tuple[SetupModel, list[Cmd]]:
        candidate = model.pending
        if model.phase != "probing" or candidate is None or candidate.name != name:
            return model, []
        failing = [detail for status, detail in rows if status == "fail"]
        if not failing:
            return self._write(model, replace(candidate, checked="passed" if rows else "unchecked"))
        form = model.forms[name].with_error(failing[0] or "the check failed")
        form = _with_buttons(form, _anyway_buttons())
        failed = replace(candidate, checked="failed")
        return (
            replace(
                model,
                phase="idle",
                pending=failed,
                failed={**model.failed, name: failed},
                rows=_status(model.rows, name, "failed"),
                forms={**model.forms, name: form},
                selected=self._index(name),
                focus="form",
            ),
            [],
        )

    def _pressed(self, model: SetupModel, button: str) -> tuple[SetupModel, list[Cmd]]:
        if button == "save":
            return model, [Cmd.send(Submit())]
        if button == "save_anyway":
            candidate = model.failed.get(model.name)
            if model.phase != "idle" or candidate is None:
                return model, []
            return self._write(model, candidate)
        # Cancel: forget the failed check and go back to the list.
        form = _reset(model.forms[model.name].with_error(""))
        return replace(
            model,
            forms={**model.forms, model.name: form},
            failed=_without(model.failed, model.name),
            focus="list",
        ), []

    def _write(self, model: SetupModel, candidate: Candidate) -> tuple[SetupModel, list[Cmd]]:
        name = candidate.name
        spec, state, store, preflight = (
            self.services[name],
            model.states[name],
            self.store,
            self.preflight,
        )

        def save() -> object:
            notes = write_candidate(spec, candidate, state, store, preflight)
            return Saved(name, candidate.profile, tuple(notes), _refreshed(spec, candidate.profile))

        return (
            replace(
                model,
                phase="saving",
                pending=candidate,
                failed=_without(model.failed, name),
                rows=_status(model.rows, name, "saving"),
            ),
            [Cmd(save, write=True, name="save")],
        )

    def _saved(self, model: SetupModel, message: Saved) -> tuple[SetupModel, list[Cmd]]:
        candidate = model.pending
        if model.phase != "saving" or candidate is None or candidate.name != message.name:
            return model, []
        name = message.name
        states = model.states if message.state is None else {**model.states, name: message.state}
        status = {"passed": "ok", "unchecked": "saved", "failed": "saved, check failed"}[
            candidate.checked
        ]
        done = replace(
            model,
            phase="idle",
            pending=None,
            failed=_without(model.failed, name),
            rows=_status(model.rows, name, status),
            states=states,
            forms={
                **model.forms,
                name: self._form(self.services[name], states[name], model.current),
            },
            saved=(*model.saved, (message.profile, name)),
            notes=(*model.notes, *message.notes),
            focus="list",
        )
        return self._after(done)

    def _failed(self, model: SetupModel, error: BaseException) -> tuple[SetupModel, list[Cmd]]:
        text = str(error).strip() or type(error).__name__
        if model.phase == "loading":
            return replace(model, phase="idle", profile=model.profile.with_error(text)), []
        candidate = model.pending
        if model.phase not in ("probing", "saving") or candidate is None:
            return model, []
        wrote = model.phase == "saving"
        failed = replace(self._fail(model, candidate.name, text), phase="idle", pending=None)
        if wrote and model.leaving is not None:
            note: Note = ("warning", f"could not save {candidate.name}: {text}")
            failed = replace(failed, notes=(*failed.notes, note))
        return self._after(failed)

    def _fail(self, model: SetupModel, name: str, text: str) -> SetupModel:
        form = _reset(model.forms[name].with_error(text))
        return replace(
            model,
            failed=_without(model.failed, name),
            rows=_status(model.rows, name, "failed"),
            forms={**model.forms, name: form},
            selected=self._index(name),
            focus="form",
        )

    def _index(self, name: str) -> int:
        return list(self.services).index(name)

    def _after(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        """A save is over: end the screen now if a quit was waiting for it."""
        if model.leaving is None:
            return model, []
        return model, [Cmd.send(Quit(_result(model, interrupted=model.leaving == "interrupt")))]

    # --- building -------------------------------------------------------------

    def _rows(self, states: Mapping[str, ServiceState]) -> tuple[CapRow, ...]:
        return tuple(CapRow(name, _initial_status(states[name])) for name in self.services)

    def _forms(self, states: Mapping[str, ServiceState], profile: str) -> dict[str, Form]:
        return {
            name: self._form(spec, states[name], profile) for name, spec in self.services.items()
        }

    def _form(self, spec: CapabilitySpec, state: ServiceState, profile: str) -> Form:
        has_command = takes_token_command(spec.profile_model)
        choices = _token_choices(spec, state, self.store, has_command=has_command)
        tabs = tuple(
            self._tab(spec, state, choice, profile, has_command=has_command) for choice in choices
        )
        return Form(
            (
                ("base_url", TextInput("Base URL", state.base_url or "", validator=_url_error)),
                ("token", Tabs("Token source", tabs, active=choices[0].value)),
                ("buttons", _save_buttons()),
            )
        )

    def _tab(
        self,
        spec: CapabilitySpec,
        state: ServiceState,
        choice: PromptChoice[str],
        profile: str,
        *,
        has_command: bool,
    ) -> Tab:
        how = choice.value
        label = self.store.name if how == "store" and self.store is not None else _TAB_LABELS[how]
        note = choice.label
        if how == "keep" and has_command and state.inherited_token:
            note += (
                f"\n{spec.config_section}.token is set in profile {DEFAULT_PROFILE}: switching "
                f"profile {profile} to a stored token or a command would leave it in charge, so "
                f"only keeping the current token is offered\n{hint('auth migrate')}"
            )
        fields: tuple[tuple[str, Any], ...] = ()
        if how in ("store", "enter"):
            fields = (("token", SecretInput("Token", validator=_token_error)),)
        elif how == "command":
            validator = _command_validator(spec.name)
            fields = (("command", TextInput("Command", validator=validator)),)
        return Tab(how, label, fields, note)


# --- helpers -------------------------------------------------------------------


def _no_preflight() -> None:
    """Nothing to test: no store was picked."""


def _initial_status(state: ServiceState) -> str:
    if state.invalid is not None:
        return "invalid settings"
    if state.configured:
        return "configured" if state.token_source is not None else "missing token"
    return "not configured"


def _without(failed: Mapping[str, Candidate], name: str) -> dict[str, Candidate]:
    """``failed`` without ``name``: that capability's failed check no longer applies."""
    return {key: candidate for key, candidate in failed.items() if key != name}


def _status(rows: tuple[CapRow, ...], name: str, status: str) -> tuple[CapRow, ...]:
    return tuple(replace(row, status=status) if row.name == name else row for row in rows)


def _field_rows(frame: Frame, field: TextInput) -> int:
    """The lines the profile field takes: its box (or label and value) and a note under it."""
    base = 3 if frame.box() is not None else 2
    return base + (1 if field.error else 0)


def _save_buttons() -> Buttons:
    return Buttons((Button("save", "Save", "primary"), Button("cancel", "Cancel", "ghost")))


def _anyway_buttons() -> Buttons:
    return Buttons(
        (Button("save_anyway", "Save anyway", "primary"), Button("cancel", "Cancel", "ghost"))
    )


def _with_buttons(form: Form, buttons: Buttons) -> Form:
    fields = tuple((name, buttons if name == "buttons" else field) for name, field in form.fields)
    return replace(form, fields=fields)


def _reset(form: Form) -> Form:
    """``form`` with ``Save`` and ``Cancel`` as its buttons again."""
    return _with_buttons(form, _save_buttons())


def _url_error(text: str) -> str:
    return "" if text.strip() else "Enter a base URL."


def _token_error(token: SecretStr) -> str:
    return "" if token.get_secret_value().strip() else "Enter a token."


def _command_validator(name: str) -> Callable[[str], str]:
    def validate(text: str) -> str:
        try:
            _token_command(name, text)
        except ConfigError as exc:
            return str(exc)
        return ""

    return validate


def _token_command(name: str, text: str) -> list[str]:
    """The token command's argv; an empty or malformed one is refused before anything is written."""
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise ConfigError(f"invalid {name} token command: {exc}", category="invalid") from exc
    if not argv:
        raise ConfigError(f"{name} token command is empty", category="invalid")
    return argv


def _candidate(spec: CapabilitySpec, profile: str, values: Mapping[str, object]) -> Candidate:
    """The form's validated values as what the user asked for."""
    tab = values["token"]
    assert isinstance(tab, dict)
    how = str(tab["tab"])
    token = None
    argv = None
    if how in ("store", "enter"):
        typed = tab["token"]
        assert isinstance(typed, SecretStr)
        token = SecretStr(typed.get_secret_value().strip())
    elif how == "command":
        argv = tuple(_token_command(spec.name, str(tab["command"])))
    return Candidate(
        spec.name,
        spec.config_section,
        profile,
        how,
        str(values["base_url"]).strip(),
        token,
        argv,
    )


def _refreshed(spec: CapabilitySpec, profile: str) -> ServiceState | None:
    """What ``profile`` resolves to now for ``spec``, or ``None`` when it cannot be read."""
    try:
        return service_states({spec.name: spec}, profile, read_config_dict())[spec.name]
    except ConfigError:
        return None


def _result(model: SetupModel, *, interrupted: bool) -> SetupResult:
    """What was written, for the profile written last (earlier profiles become a note)."""
    profile = model.saved[-1][0] if model.saved else model.current
    by_profile: dict[str, list[str]] = {}
    for written, name in model.saved:
        names = by_profile.setdefault(written, [])
        if name not in names:
            names.append(name)
    others: list[Note] = [
        ("info", f"also set up {', '.join(names)} in profile {other}; {_check_hint(other)}")
        for other, names in by_profile.items()
        if other != profile
    ]
    touched = tuple(by_profile.get(profile, ()))
    return SetupResult(profile, touched, (*model.notes, *others), interrupted)


def _check_hint(profile: str) -> str:
    return hint(f"--profile {profile} doctor --online")


def _token_choices(
    spec: CapabilitySpec,
    current: ServiceState,
    store: TokenStore | None,
    *,
    has_command: bool,
) -> list[PromptChoice[str]]:
    """The token choices, the recommended one first; none stores plain text if avoidable.

    While ``default``'s plaintext token wins, keeping it is the only choice
    that would work for a section ``auth`` serves.
    """
    choices: list[PromptChoice[str]] = []
    if current.token_source is not None:
        label = f"Keep the current token ({current.token_source})"
        choices.append(PromptChoice(value="keep", label=label))
    if has_command and current.inherited_token:
        return choices
    if current.plaintext is not None and store is not None:
        label = f"Move the current token to {store.name}"
        choices.insert(0, PromptChoice(value="move", label=label))
    if store is not None:
        choices.append(PromptChoice(value="store", label=f"Store a token with {store.name}"))
    if has_command:
        choices.append(PromptChoice(value="command", label="Run a command that prints the token"))
        env = token_env_names(spec.profile_model.model_construct())
        # default's command would win over the variable once the profile's is gone.
        if env and not current.inherited_command:
            stored = current.own_command is not None and preset_entry(current.own_command)
            effect = "drops the stored token" if stored else "nothing is stored"
            label = f"Use ${env[0]} (you export it; {effect})"
            choices.append(PromptChoice(value="env", label=label))
    else:
        choices.append(PromptChoice(value="enter", label="Enter a token (stored in config.yml)"))
    return choices
