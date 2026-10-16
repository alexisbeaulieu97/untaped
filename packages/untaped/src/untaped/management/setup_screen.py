"""The ``untaped setup`` screen: its model, update and view.

Two panes under a profile field. The left lists this install, then every
settings section: the services first (those that still need something above
the configured ones, each with a status), then core's sections and the other
plugins' (each with its plugin's stability mark, or ``invalid settings``); the
order is fixed when the screen opens, so a saved service does not jump. The
right shows the selected section's settings
(:mod:`untaped.management.setup_sections`): enter opens a row's field and
writes it as ``config set`` does, ``delete`` unsets it. What the section
names, the screen does not know: rows come from the config use cases, fields
from each setting's shape.

A service's first row is its connection: enter opens its form (base URL, a
token source as tabs, buttons; :mod:`untaped.management.setup_connection`).
Saving it checks before it writes: the form's values become a
:class:`Candidate`, the plugin's online checks run against them inside a
settings overlay (nothing is stored yet,
:func:`untaped.management.doctor.online_probe_rows`), and only a pass runs the
write, as a write command, so quitting never leaves it half done. A failed
check shows its reason under the form and offers ``Save anyway``, for that
plugin until its form changes or saves (each plugin keeps its own). A Command
token source is run first, on every check, as a suspend command, so a ``pass``
or ``op`` prompt gets the real terminal; its token then sits in the process
cache for the background check. Leaving the profile field loads the profile
typed there, so the field and the rows never disagree; a profile that does
not exist yet shows ``default``'s values, and its first write creates it.

The list's first row is this install: whether it is registered (``untaped
setup register``), so the cleanup of store layouts keeps the ones it uses;
its form's ``Register`` button registers it. The cursor starts on the first
section.

What a write has to say (a key set, a profile created, a token replaced or to
export) comes back as notes, which the command prints after the screen closes
with the doctor rows of the services whose connection was saved
(:class:`SetupResult`). Esc and ctrl-c on the list end the screen with that
result (ctrl-c flagged ``interrupted``, which ``setup`` turns into exit 130);
neither is a cancel. A quit while a write runs waits for it, so the result
always says what was written.

The typed token lives in a ``SecretInput`` and a ``Candidate`` as a ``SecretStr``
and in no frame, repr or message of this module's own. The model never holds the
plain text of a token the profile already stores (``ServiceState.plaintext`` is
left out of its repr), nor a secret setting's value (rows show it masked).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from typing import Literal

from rich.console import Group, RenderableType
from rich.text import Text

from untaped.auth import CommandToken, forget_token_command
from untaped.config.use_cases import Note
from untaped.errors import ConfigError
from untaped.management.doctor import online_probe_rows
from untaped.management.setup_connection import (
    anyway_buttons,
    candidate,
    connection_form,
    reset,
    with_buttons,
)
from untaped.management.setup_plan import SETUP_ALTERNATIVE, SETUP_COMMAND
from untaped.management.setup_sections import (
    CONNECTION,
    FieldOpened,
    Listing,
    SectionPane,
    SettingRow,
    UnsetAsked,
    Written,
    connection_row,
    field_for,
    open_command,
    read_listing,
    section_mark,
    write_command,
)
from untaped.management.setup_state import ServiceState
from untaped.management.setup_write import Candidate, overlay_values, write_candidate
from untaped.messages import hint, shown_path
from untaped.plugins.registry import CompositionResult, PluginSpec
from untaped.screen.components.buttons import Button, Buttons, Pressed
from untaped.screen.components.choices import ListItem, SingleList
from untaped.screen.components.draw import role_style
from untaped.screen.components.form import Form, Submitted
from untaped.screen.components.inputs import COMPLETION_ROWS, TextInput
from untaped.screen.components.layout import Panes
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
from untaped.token_store import TokenStore

__all__ = ["SetupModel", "SetupResult", "setup_screen"]

type Focus = Literal["profile", "list", "section", "form"]
"""``section``: the selected section's rows; ``form``: a connection's or this install's form."""
type Phase = Literal["idle", "loading", "probing", "saving", "registering", "opening", "writing"]
#: The list row of this install's registration; never a section name (it has a space).
INSTALL_ROW = "this install"
#: What the profile field says under it while the profile does not exist yet.
NEW_PROFILE = "new profile: its first write creates it"

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
    "registered": "screen.success",
    "not registered": "screen.accent",
    "source tree": "screen.muted",
    "registering": "screen.accent",
    "experimental": "screen.muted",
    "deprecated": "screen.accent",
    "": "screen.muted",
}
_PROGRESS = ("checking", "saving", "registering")


@dataclass(frozen=True)
class SetupResult:
    """What the screen did: for ``profile``, the services saved and the lines to print.

    ``touched`` are the services whose connection was saved, whose doctor rows
    ``setup`` prints; ``written`` every key written or unset on its own.
    ``notes`` are what the writes had to say, printed after the screen closes.
    ``interrupted`` is ctrl-c, which ``setup`` reports as exit 130 after printing
    the rest. ``registered``: this install was registered from its row.
    """

    profile: str
    touched: tuple[str, ...]
    notes: tuple[Note, ...]
    interrupted: bool = False
    registered: bool = False
    written: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapRow:
    """One row on the left: a section's name (or this install) and its status."""

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
    """The connection write for ``name`` finished; ``listing`` is what ``profile`` shows now."""

    name: str
    profile: str
    notes: tuple[Note, ...]
    listing: Listing | None


@dataclass(frozen=True)
class Registered:
    """The registration finished; ``text`` is what it wrote."""

    text: str


@dataclass(frozen=True)
class ProfileLoaded:
    """What ``profile`` shows, read for the profile field."""

    profile: str
    listing: Listing


@dataclass(frozen=True)
class SetupModel:
    """Everything the screen shows and every decision it holds."""

    profile: TextInput
    """The profile field (what is typed; ``current`` is the profile the rows are for)."""
    current: str
    rows: tuple[CapRow, ...]
    states: Mapping[str, ServiceState]
    forms: Mapping[str, Form]
    """This install's form and each service's connection form."""
    panes: Mapping[str, SectionPane]
    """Each listed section's rows."""
    config_path: str = ""
    selected: int = 0
    focus: Focus = "list"
    phase: Phase = "idle"
    pending: Candidate | None = None
    """The candidate being checked or saved, or the one whose check failed."""
    failed: Mapping[str, Candidate] = field(default_factory=dict)
    """Per plugin, the candidate whose check failed: its form offers ``Save anyway``."""
    leaving: Literal["quit", "interrupt"] | None = None
    """A quit asked for while a write runs: it ends the screen once the write finished."""
    saved: tuple[tuple[str, str], ...] = ()
    """``(profile, plugin)`` for every connection write that finished."""
    written: tuple[tuple[str, str], ...] = ()
    """``(profile, key)`` for every key written or unset from a section's rows."""
    notes: tuple[Note, ...] = ()
    registered: bool = False
    """Whether this run registered this install (its row's ``Register``)."""

    @property
    def name(self) -> str:
        """The selected row: a section, or :data:`INSTALL_ROW`."""
        return self.rows[self.selected].name

    @property
    def pane(self) -> SectionPane | None:
        """The selected section's rows (``None`` on this install's row)."""
        return self.panes.get(self.name)


def setup_screen(
    result: CompositionResult,
    services: Mapping[str, PluginSpec],
    *,
    sections: Sequence[str],
    profile: str,
    store: TokenStore | None,
    profiles: Sequence[str] = (),
) -> Screen[SetupModel, SetupResult]:
    """The setup screen for ``sections`` (``services`` among them) in ``profile``.

    ``store`` is the token store picked once for the run (``None`` when none
    works or no service takes a command); ``profiles`` the names the profile
    field completes.
    """
    app = _Setup(result, dict(services), tuple(sections), store, tuple(profiles), profile)
    return Screen(
        init=app.init,
        update=app.update,
        view=app.view,
        title="Set up untaped",
        command=SETUP_COMMAND,
        alternative=SETUP_ALTERNATIVE,
        keys=(
            Binding("up", "previous", None, when=_moving),
            Binding("down", "next", None, when=_moving),
            Binding("delete", "unset", None, when=_unsettable),
            Binding("left", "previous choice", None, when=lambda model: model.focus == "form"),
            Binding("right", "next choice", None, when=lambda model: model.focus == "form"),
        ),
        shared_labels=_SHARED_LABELS,
        layout="full",
    )


def _register() -> object:
    from untaped.refs import register_this_install  # noqa: PLC0415 - off the startup path

    ref, _changed = register_this_install()
    held = ", ".join(ref.targets) or "no stores"
    return Registered(f"registered this install, {ref.describe()} ({held})")


def _install_form() -> Form:
    """This install's form: what registering does, and ``Register`` while it is not."""
    from untaped.refs import environment, install_state, this_install  # noqa: PLC0415

    state, ref, env = install_state(), this_install(), environment()
    if state == "source tree" or env is None:
        text = "This untaped runs from a source tree: only an installed untaped can be registered."
        return Form((("about", _Note(text)),))
    if state == "registered" and ref is not None:
        text = (
            f"Registered: {ref.describe()}. Its runs remove the store layouts no registered "
            "install uses. `untaped setup register --remove` takes it back."
        )
        return Form((("about", _Note(text)),))
    text = (
        f"Register {shown_path(env)} so its runs remove the store layouts no registered "
        "install uses, and keep the ones it does (`untaped setup register`)."
    )
    buttons = Buttons((Button("register", "Register", "primary"),))
    return Form((("about", _Note(text)), ("buttons", buttons)), focus=1)


def _browsing(model: SetupModel) -> bool:
    """Whether the keyboard is on a section's rows, with no field open and nothing asked."""
    pane = model.pane
    return (
        model.focus == "section" and pane is not None and pane.editing is None and not pane.confirm
    )


def _moving(model: SetupModel) -> bool:
    """Whether up and down move a cursor: the list's, or a section's rows'."""
    return model.focus == "list" or _browsing(model)


def _unsettable(model: SetupModel) -> bool:
    """Whether ``delete`` asks to unset the focused row (the profile sets it)."""
    pane = model.pane
    row = None if pane is None else pane.row
    return _browsing(model) and row is not None and row.own and bool(row.key)


def _tab_label(model: SetupModel) -> str | None:
    """Tab walks the profile field, the list and the section; in a form it is the default."""
    if model.focus == "list":
        return "form" if model.name == INSTALL_ROW else "settings"
    return "list" if model.focus == "profile" else None


def _enter_label(model: SetupModel) -> str | None:
    """Enter loads the profile, opens a section, or opens and writes a row's field."""
    if model.focus == "profile":
        return "load"
    if model.focus == "list":
        return "open"
    pane = model.pane
    if model.focus == "form" or pane is None:
        return None
    if pane.editing is not None:
        return "write"
    row = pane.row
    if row is None or pane.confirm:
        return None
    if not row.key:
        return "open"
    return "edit" if row.descriptor is not None else None


def _save_label(model: SetupModel) -> str | None:
    """Ctrl-s saves a form (checking first) or writes the open field.

    On this install's row it registers, while there is something to register.
    """
    if model.focus == "profile":
        return None
    if model.name == INSTALL_ROW:
        return "register" if _registrable(model) else None
    if model.focus == "form":
        return "save"
    pane = model.pane
    return "write" if model.focus == "section" and pane and pane.editing is not None else None


def _registrable(model: SetupModel) -> bool:
    """Whether this install's row offers ``Register`` now."""
    return _row_status(model.rows, INSTALL_ROW) == "not registered" and model.phase == "idle"


def _row_status(rows: tuple[CapRow, ...], name: str) -> str:
    return next(row.status for row in rows if row.name == name)


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
        services: dict[str, PluginSpec],
        sections: tuple[str, ...],
        store: TokenStore | None,
        profiles: tuple[str, ...],
        profile: str,
    ) -> None:
        self.result = result
        self.services = services
        self.sections = sections
        self.store = store
        self.profiles = profiles
        self.profile = profile
        self.plugins = frozenset(registered.spec.name for registered in result.plugins)
        # Tested once, just before the first token goes to the store.
        self.preflight = cache(store.preflight) if store is not None else _no_preflight
        self.order: tuple[str, ...] = ()

    # --- init and view --------------------------------------------------------

    def init(self) -> tuple[SetupModel, list[Cmd]]:
        field = TextInput(
            "Profile",
            self.profile,
            complete=lambda text: [name for name in self.profiles if name.startswith(text)],
        )
        from untaped.refs import install_state  # noqa: PLC0415 - off the startup path

        listing = read_listing(self.profile, self.services)
        self.order = self._order(listing)
        rows = (
            CapRow(INSTALL_ROW, install_state()),
            *(CapRow(name, self._status(name, listing)) for name in self.order),
        )
        model = SetupModel(
            profile=_profile_field(field, listing),
            current=self.profile,
            rows=rows,
            states=listing.states,
            forms={INSTALL_ROW: _install_form(), **self._forms(listing.states, self.profile)},
            panes=self._panes(listing, self.profile),
            config_path=str(resolve_config_path()),
            selected=1 if self.order else 0,
        )
        return model, []

    def view(self, model: SetupModel, frame: Frame) -> RenderableType:
        muted = role_style(frame, "screen.muted")
        header = Text()
        header.append(" untaped setup ", style=role_style(frame, "screen.highlight"))
        header.append(f"  config {model.config_path}", style=muted)
        field_width = min(frame.width, 40)
        field = model.profile.view(frame, focused=model.focus == "profile", width=field_width)
        used = 1 + _field_rows(frame, model.profile, focused=model.focus == "profile")
        inner = replace(frame, height=max(1, frame.height - used))
        left = SingleList(
            "",
            tuple(self._item(frame, row) for row in model.rows),
            value=model.name,
            cursor=model.selected,
            window_rows=max(3, inner.height - 2),  # the pane's height, less its border
        )
        right: Form | SectionPane | _Note
        title = model.name
        if model.name == INSTALL_ROW or model.focus == "form":
            right = model.forms[model.name]
            title = model.name if model.name == INSTALL_ROW else f"{model.name} {CONNECTION}"
        else:
            right = model.pane or _Note("Nothing to set in this section.")
        panes = Panes(
            left,
            right,
            focus=0 if model.focus == "list" else 1,
            left_title="Setup",
            right_title=title,
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
        """A form's submit, a button, a row's answer, or what a command brought back."""
        match message:
            case Submitted() if model.name == INSTALL_ROW:
                return self._register(model)
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
            case ProfileLoaded(profile=profile, listing=listing):
                return self._loaded(model, profile, listing)
            case Registered(text=text):
                return self._registered(model, text)
            case FieldOpened() | UnsetAsked() | Written():
                return self._row_answer(model, message)
            case CmdError(error=error):
                return self._failed(model, error)
        return model, []

    # --- keys -----------------------------------------------------------------

    def _edit(self, model: SetupModel, message: Key | Paste) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "profile":
            if model.phase != "idle":
                return model, []  # the field and the rows must agree: no typing mid-load or save
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
        if model.focus == "section":
            return self._in_pane(model, message)
        return self._in_form(model, message)

    def _in_pane(self, model: SetupModel, message: object) -> tuple[SetupModel, list[Cmd]]:
        """Give ``message`` to the selected section's rows (not while a read or write runs)."""
        pane = model.pane
        if pane is None or model.phase != "idle":
            return model, []
        updated, cmds = pane.update(message)
        if updated is pane and not cmds:
            return model, []
        return _with_pane(model, model.name, updated), cmds

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
            updated = reset(updated.with_error(""))
            model = replace(model, failed=_without(model.failed, model.name))
        return replace(model, forms={**model.forms, model.name: updated}), list(cmds)

    def _next(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "profile":
            return self._leave_profile(model, "list")
        if model.focus == "list":
            return replace(model, focus=_opened_focus(model)), []
        if model.focus == "section":
            return model, []
        return self._in_form(model, NextField())

    def _previous(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "form":
            moved, cmds = self._in_form(model, PrevField())
            if moved is model and not cmds:
                return replace(model, focus=_closed_focus(model)), []  # at its first field
            return moved, cmds
        if model.focus == "section":
            return replace(model, focus="list"), []
        if model.focus == "list":
            return replace(model, focus="profile"), []
        return self._leave_profile(model, "profile")

    def _activate(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "profile":
            return self._load(model)
        if model.focus == "list":
            return replace(model, focus=_opened_focus(model)), []
        if model.focus == "section":
            return self._open(model)
        return self._in_form(model, Activate())

    def _submit(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "profile":
            # A typed profile is loaded first (as on enter); saving is a second ctrl-s, once
            # the rows are the new profile's.
            return self._leave_profile(model, "profile")
        if model.focus == "section":
            pane = model.pane
            return self._write(model) if pane and pane.editing is not None else (model, [])
        if model.name != INSTALL_ROW and model.focus == "list":
            return model, []  # a section is written a field at a time, from its rows
        return self._in_form(replace(model, focus="form"), Submit())

    def _back(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        if model.focus == "form":
            return replace(model, focus=_closed_focus(model)), []
        if model.focus == "section":
            pane = model.pane
            if pane is not None and (pane.editing is not None or pane.confirm):
                return _with_pane(model, model.name, pane.closed()), []
            return replace(model, focus="list"), []
        return self._leave(model, "quit")

    def _leave(
        self, model: SetupModel, how: Literal["quit", "interrupt"]
    ) -> tuple[SetupModel, list[Cmd]]:
        """End the screen with the result, once a running write has finished."""
        if model.phase in ("saving", "registering", "writing"):
            return replace(model, leaving=how), []
        return model, [Cmd.send(Quit(_result(model, interrupted=how == "interrupt")))]

    # --- profile --------------------------------------------------------------

    def _leave_profile(self, model: SetupModel, then: Focus) -> tuple[SetupModel, list[Cmd]]:
        """The profile field loses the keyboard: what is typed there is loaded, as on enter.

        The field and the rows never disagree: a name that differs from ``current`` loads (and
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
            return ProfileLoaded(name, read_listing(name, services))

        return replace(model, phase="loading"), [Cmd(load_profile, name="load_profile")]

    def _loaded(
        self, model: SetupModel, profile: str, listing: Listing
    ) -> tuple[SetupModel, list[Cmd]]:
        if model.phase != "loading":
            return model, []
        rows = tuple(CapRow(name, self._status(name, listing)) for name in self.order)
        return (
            replace(
                model,
                profile=_profile_field(model.profile, listing),
                current=profile,
                rows=(model.rows[0], *rows),
                states=listing.states,
                forms={
                    INSTALL_ROW: model.forms[INSTALL_ROW],
                    **self._forms(listing.states, profile),
                },
                panes=self._panes(listing, profile),
                phase="idle",
                pending=None,
                failed={},
                focus="list",
            ),
            [],
        )

    # --- a section's rows -----------------------------------------------------

    def _row_answer(
        self, model: SetupModel, message: FieldOpened | UnsetAsked | Written
    ) -> tuple[SetupModel, list[Cmd]]:
        """A row's field was read, its unset confirmed, or its write finished."""
        match message:
            case FieldOpened():
                return self._opened(model, message)
            case UnsetAsked(key=key):
                return self._unset(model, key)
            case Written():
                return self._written(model, message)

    def _open(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        """Enter on a row: write its open field, open the connection's form, or read its field."""
        pane = model.pane
        if pane is None or model.phase != "idle" or pane.confirm:
            return model, []
        if pane.editing is not None:
            return self._write(model)
        row = pane.row
        if row is None:
            return model, []
        if not row.key:
            return replace(model, focus="form"), []  # the connection row
        if row.descriptor is None:
            return model, []  # read-only
        return replace(model, phase="opening"), [open_command(model.current, row)]

    def _opened(self, model: SetupModel, message: FieldOpened) -> tuple[SetupModel, list[Cmd]]:
        if model.phase != "opening":
            return model, []
        section = _section(message.key)
        pane = model.panes.get(section)
        opened = model if pane is None else _with_pane(model, section, field_for(pane, message))
        return replace(opened, phase="idle"), []

    def _write(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        """Write the open field, as ``config set`` would; its own check first."""
        pane = model.pane
        row = None if pane is None else pane.row
        if pane is None or row is None or model.phase != "idle":
            return model, []
        refused = pane.checked()
        if refused is not None:
            return _with_pane(model, model.name, refused), []
        value = pane.to_write()
        cmd = write_command(model.current, row.key, value, pane.expected, self.services)
        return replace(model, phase="writing"), [cmd]

    def _unset(self, model: SetupModel, key: str) -> tuple[SetupModel, list[Cmd]]:
        if model.phase != "idle":
            return model, []
        cmd = write_command(model.current, key, None, None, self.services)
        return replace(model, phase="writing"), [cmd]

    def _written(self, model: SetupModel, message: Written) -> tuple[SetupModel, list[Cmd]]:
        if model.phase != "writing":
            return model, []
        section = _section(message.key)
        done = replace(model, phase="idle")
        if message.listing is not None:
            done = self._relisted(done, message.listing)
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
            return self._after(done)
        if pane is not None:
            done = _with_pane(done, section, pane.closed())
        written = ((model.current, message.key),) if message.changed else ()
        done = replace(done, written=(*done.written, *written), notes=(*done.notes, *message.notes))
        return self._after(done)

    def _relisted(self, model: SetupModel, listing: Listing) -> SetupModel:
        """``model`` over re-read ``listing``: rows, statuses and states; open fields stay."""
        rows = tuple(
            row if row.name == INSTALL_ROW else replace(row, status=self._restatus(row, listing))
            for row in model.rows
        )
        panes = {
            name: pane.with_rows(self._rows_of(name, listing), listing.errors.get(name, ""))
            for name, pane in model.panes.items()
        }
        return replace(
            model,
            rows=rows,
            states=listing.states,
            panes=panes,
            profile=_profile_field(model.profile, listing),
        )

    def _restatus(self, row: CapRow, listing: Listing) -> str:
        """A row's status after a write: a service's stays, unless its validity changed."""
        state = listing.states.get(row.name)
        if state is None:
            return self._status(row.name, listing)
        if state.invalid is not None or row.status == "invalid settings":
            return _initial_status(state)
        return row.status

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
            asked = candidate(spec, model.current, values)
        except ConfigError as exc:
            return self._fail(model, name, str(exc)), []

        def prime() -> object:
            # Runs with the real terminal; the token is cached for the probe.
            section = spec.name
            argv = list(asked.argv or ())
            forget_token_command(argv)  # every check runs the command, a retry included
            try:
                CommandToken(argv, section=section).get_secret_value()
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
        if asked.how == "command":
            return checking, [Cmd(prime, suspend=True, name="prime")]
        return checking, [self._probe(asked, model.states[name])]

    def _primed(self, model: SetupModel, name: str) -> tuple[SetupModel, list[Cmd]]:
        asked = model.pending
        if model.phase != "probing" or asked is None or asked.name != name:
            return model, []
        return model, [self._probe(asked, model.states[name])]

    def _probe(self, asked: Candidate, state: ServiceState) -> Cmd:
        """The background check of ``asked``: the plugin's online checks over its values."""
        overlay = overlay_values(asked, state)
        result, name, profile = self.result, asked.name, asked.profile

        def probe() -> object:
            rows = online_probe_rows(result, profile, name, overlay)
            return Probed(name, tuple((str(row["status"]), str(row["detail"])) for row in rows))

        return Cmd(probe, name="probe")

    def _probed(
        self, model: SetupModel, name: str, rows: tuple[tuple[str, str], ...]
    ) -> tuple[SetupModel, list[Cmd]]:
        asked = model.pending
        if model.phase != "probing" or asked is None or asked.name != name:
            return model, []
        failing = [detail for status, detail in rows if status == "fail"]
        if not failing:
            return self._save(model, replace(asked, checked="passed" if rows else "unchecked"))
        form = model.forms[name].with_error(failing[0] or "the check failed")
        form = with_buttons(form, anyway_buttons())
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

    def _pressed(self, model: SetupModel, button: str) -> tuple[SetupModel, list[Cmd]]:
        if button == "register":
            return self._register(model)
        if button == "save":
            return model, [Cmd.send(Submit())]
        if button == "save_anyway":
            asked = model.failed.get(model.name)
            if model.phase != "idle" or asked is None:
                return model, []
            return self._save(model, asked)
        # Cancel: forget the failed check and go back to the section's rows.
        form = reset(model.forms[model.name].with_error(""))
        return replace(
            model,
            forms={**model.forms, model.name: form},
            failed=_without(model.failed, model.name),
            focus=_closed_focus(model),
        ), []

    def _save(self, model: SetupModel, asked: Candidate) -> tuple[SetupModel, list[Cmd]]:
        name = asked.name
        spec, state, store, preflight, services = (
            self.services[name],
            model.states[name],
            self.store,
            self.preflight,
            self.services,
        )

        def save() -> object:
            notes = write_candidate(spec, asked, state, store, preflight)
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

    def _saved(self, model: SetupModel, message: Saved) -> tuple[SetupModel, list[Cmd]]:
        asked = model.pending
        if model.phase != "saving" or asked is None or asked.name != message.name:
            return model, []
        name = message.name
        status = {"passed": "ok", "unchecked": "saved", "failed": "saved, check failed"}[
            asked.checked
        ]
        done = replace(model, phase="idle", rows=_status(model.rows, name, status))
        if message.listing is not None:
            done = self._relisted(done, message.listing)
        state = done.states[name]
        done = replace(
            done,
            pending=None,
            failed=_without(model.failed, name),
            forms={
                **done.forms,
                name: connection_form(self.services[name], state, self.store, model.current),
            },
            saved=(*model.saved, (message.profile, name)),
            notes=(*model.notes, *message.notes),
            focus="section",
        )
        return self._after(done)

    # --- this install ---------------------------------------------------------

    def _register(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        """``Register`` pressed on this install's row: write its reference in the background."""
        if not _registrable(model):
            return model, []
        registering = replace(
            model,
            phase="registering",
            rows=_status(model.rows, INSTALL_ROW, "registering"),
            forms={**model.forms, INSTALL_ROW: model.forms[INSTALL_ROW].with_error("")},
        )
        return registering, [Cmd(_register, write=True, name="register")]

    def _registered(self, model: SetupModel, text: str) -> tuple[SetupModel, list[Cmd]]:
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
        return self._after(done)

    def _failed(self, model: SetupModel, error: BaseException) -> tuple[SetupModel, list[Cmd]]:
        text = str(error).strip() or type(error).__name__
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
            return self._after(failed)
        if model.phase == "loading":
            return replace(model, phase="idle", profile=model.profile.with_error(text)), []
        if model.phase in ("opening", "writing"):
            pane = model.pane
            stopped = replace(model, phase="idle")
            if pane is not None:
                stopped = _with_pane(stopped, model.name, pane.with_error(text))
            return self._after(stopped)
        asked = model.pending
        if model.phase not in ("probing", "saving") or asked is None:
            return model, []
        wrote = model.phase == "saving"
        failed = replace(self._fail(model, asked.name, text), phase="idle", pending=None)
        if wrote and model.leaving is not None:
            note: Note = ("warning", f"could not save {asked.name}: {text}")
            failed = replace(failed, notes=(*failed.notes, note))
        return self._after(failed)

    def _fail(self, model: SetupModel, name: str, text: str) -> SetupModel:
        form = reset(model.forms[name].with_error(text))
        return replace(
            model,
            failed=_without(model.failed, name),
            rows=_status(model.rows, name, "failed"),
            forms={**model.forms, name: form},
            selected=_index(model.rows, name),
            focus="form",
        )

    def _after(self, model: SetupModel) -> tuple[SetupModel, list[Cmd]]:
        """A write is over: end the screen now if a quit was waiting for it."""
        if model.leaving is None:
            return model, []
        return model, [Cmd.send(Quit(_result(model, interrupted=model.leaving == "interrupt")))]

    # --- building -------------------------------------------------------------

    def _order(self, listing: Listing) -> tuple[str, ...]:
        """The sections on the left, fixed for the run.

        The services that still need something, the configured ones, core's
        sections, then the other plugins' alphabetically; a section with no
        row to show (only unset deprecated settings) is left out.
        """
        services = [name for name in self.services if name in self.sections]
        needs = [name for name in services if self._status(name, listing) != "configured"]
        configured = [name for name in services if name not in needs]
        others = [
            name for name in self.sections if name not in self.services and name in listing.rows
        ]
        core = [name for name in others if name not in self.plugins]
        plugins = sorted(name for name in others if name in self.plugins)
        return (*needs, *configured, *core, *plugins)

    def _status(self, name: str, listing: Listing) -> str:
        state = listing.states.get(name)
        if state is not None:
            return _initial_status(state)
        return "invalid settings" if name in listing.errors else section_mark(name)

    def _rows_of(self, name: str, listing: Listing) -> tuple[SettingRow, ...]:
        rows = listing.rows.get(name, ())
        state = listing.states.get(name)
        return rows if state is None else (connection_row(state), *rows)

    def _panes(self, listing: Listing, profile: str) -> dict[str, SectionPane]:
        return {
            name: SectionPane(
                name, profile, self._rows_of(name, listing), error=listing.errors.get(name, "")
            )
            for name in self.order
        }

    def _forms(self, states: Mapping[str, ServiceState], profile: str) -> dict[str, Form]:
        return {
            name: connection_form(spec, states[name], self.store, profile)
            for name, spec in self.services.items()
        }


# --- helpers -------------------------------------------------------------------


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


def _no_preflight() -> None:
    """Nothing to test: no store was picked."""


def _section(key: str) -> str:
    """The section a key belongs to: its first segment."""
    return key.partition(".")[0]


def _listing(profile: str, services: Mapping[str, PluginSpec]) -> Listing | None:
    """What ``profile`` shows now, or ``None`` when the config cannot be read."""
    try:
        return read_listing(profile, services)
    except ConfigError:
        return None


def _profile_field(field: TextInput, listing: Listing) -> TextInput:
    """The profile field saying, under it, whether its profile is still to be created."""
    help = "" if listing.exists else NEW_PROFILE
    return field if field.help == help else replace(field, help=help)


def _opened_focus(model: SetupModel) -> Focus:
    """Where enter or tab on the list goes: this install's form, or the section's rows."""
    return "form" if model.name == INSTALL_ROW else "section"


def _closed_focus(model: SetupModel) -> Focus:
    """Where leaving a form goes: back to the list from this install, else to the rows."""
    return "list" if model.name == INSTALL_ROW else "section"


def _with_pane(model: SetupModel, name: str, pane: SectionPane) -> SetupModel:
    return replace(model, panes={**model.panes, name: pane})


def _index(rows: tuple[CapRow, ...], name: str) -> int:
    return next(index for index, row in enumerate(rows) if row.name == name)


def _initial_status(state: ServiceState) -> str:
    if state.invalid is not None:
        return "invalid settings"
    if state.configured:
        return "configured" if state.token_source is not None else "missing token"
    return "not configured"


def _without(failed: Mapping[str, Candidate], name: str) -> dict[str, Candidate]:
    """``failed`` without ``name``: that plugin's failed check no longer applies."""
    return {key: asked for key, asked in failed.items() if key != name}


def _status(rows: tuple[CapRow, ...], name: str, status: str) -> tuple[CapRow, ...]:
    return tuple(replace(row, status=status) if row.name == name else row for row in rows)


def _field_rows(frame: Frame, field: TextInput, *, focused: bool) -> int:
    """The lines the profile field takes: its box (or label and value) and a note under it.

    While it is focused and completing, the candidates count too, under a rule when boxed.
    """
    boxed = frame.box() is not None
    rows = (3 if boxed else 2) + (1 if field.error or field.help else 0)
    if focused and field.completing:
        rows += min(COMPLETION_ROWS, len(field.matches)) + (1 if boxed else 0)
    return rows


def _result(model: SetupModel, *, interrupted: bool) -> SetupResult:
    """What was written, for the profile written last (earlier profiles become a note)."""
    profile = model.saved[-1][0] if model.saved else model.current
    by_profile: dict[str, list[str]] = {}
    for saved, name in model.saved:
        names = by_profile.setdefault(saved, [])
        if name not in names:
            names.append(name)
    others: list[Note] = [
        ("info", f"also set up {', '.join(names)} in profile {other}; {_check_hint(other)}")
        for other, names in by_profile.items()
        if other != profile
    ]
    touched = tuple(by_profile.get(profile, ()))
    notes = (*model.notes, *others)
    written = tuple(dict.fromkeys(key for _, key in model.written))
    return SetupResult(
        profile, touched, notes, interrupted, registered=model.registered, written=written
    )


def _check_hint(profile: str) -> str:
    return hint(f"--profile {profile} doctor --online")
