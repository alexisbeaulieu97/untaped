"""The setup screen's model: what it shows, the messages its commands send, and its run.

:class:`SetupModel` is everything the screen shows and decides;
:class:`SetupRun` the pieces of one run the screen's functions read, fixed
when it opens. The functions over them live by concern: navigation
(:mod:`untaped.core.commands.setup_screen_navigation`), a section's rows
(``setup_screen_rows``) and saving a connection or registering this install
(``setup_screen_save``) take ``(run, model, message)`` and return
``(model, cmds)``; the section listing (``setup_screen_listing``) builds
rows, panes and forms from what a profile shows. This module imports none
of them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

from untaped.core.commands.doctor import Check, run_line
from untaped.core.commands.setup_sections import Listing, SectionPane
from untaped.core.commands.setup_state import ServiceState
from untaped.core.commands.setup_write import Candidate
from untaped.core.config.use_cases import Note
from untaped.runtime.registry import CompositionResult
from untaped.screen.components.form import Form
from untaped.screen.components.inputs import TextInput
from untaped.sdk.network._token_store import TokenStore
from untaped.sdk.words import command_argv, hint

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin

type Focus = Literal["profile", "list", "section", "form"]
"""``section``: the selected section's rows; ``form``: a connection's or this install's form."""
type Phase = Literal["idle", "loading", "probing", "saving", "registering", "opening", "writing"]
#: The list row of this install's registration; never a section name (it has a space).
INSTALL_ROW = "this install"
#: What the profile field says under it while the profile does not exist yet.
NEW_PROFILE = "new profile: its first write creates it"


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
class NotSaved:
    """The connection write for ``name`` failed with ``error``; ``listing`` is what ``profile``
    shows now (what was written before the failure stays). ``conflict``: it was refused because
    the profile changed while the form was open. ``written``: the settings the profile now holds
    as the save asked, written before it failed."""

    name: str
    error: str
    conflict: bool
    listing: Listing | None
    written: tuple[str, ...] = ()


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
    opened: Mapping[str, ServiceState]
    """Each service's state its form was built from: what a save compares the form with, so
    a value relisted since is never written back from a stale form."""
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


@dataclass(frozen=True)
class SetupRun:
    """The pieces of one run the screen's functions read, fixed when it opens.

    ``services`` are the sections with a connection, ``sections`` every
    section the run shows; ``store`` the token store picked for the run
    (``None`` when none works or no service takes a command); ``profiles`` the
    names the profile field completes and ``profile`` the one it opens on.
    ``plugins`` are the composed plugins' names (not core's), ``owners`` the
    plugins a ranking may name. ``preflight`` tests the store once, just
    before the first token goes to it: it keeps that state for the run.
    ``order`` is the sections on the left, fixed for the run
    (:func:`~untaped.core.commands.setup_screen_listing.section_order`).
    """

    result: CompositionResult
    services: Mapping[str, ComposedPlugin]
    sections: tuple[str, ...]
    store: TokenStore | None
    profiles: tuple[str, ...]
    profile: str
    plugins: frozenset[str]
    owners: tuple[str, ...]
    preflight: Callable[[], None]
    order: tuple[str, ...]


# --- helpers -------------------------------------------------------------------


def _on_buttons(form: Form) -> Form:
    """``form`` focused on its buttons, where its error under them stays in sight."""
    return replace(form, focus=len(form.fields) - 1)


def _error_text(error: BaseException) -> str:
    """How a failed command reads on the screen: its message, else its type's name."""
    return str(error).strip() or type(error).__name__


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


def _without(failed: Mapping[str, Candidate], name: str) -> dict[str, Candidate]:
    """``failed`` without ``name``: that plugin's failed check no longer applies."""
    return {key: asked for key, asked in failed.items() if key != name}


def _status(rows: tuple[CapRow, ...], name: str, status: str) -> tuple[CapRow, ...]:
    return tuple(replace(row, status=status) if row.name == name else row for row in rows)


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
    return hint(run_line(command_argv(Check.spelled("--online"), profile=profile)))


def _registrable(model: SetupModel) -> bool:
    """Whether this install's row offers ``Register`` now."""
    return _row_status(model.rows, INSTALL_ROW) == "not registered" and model.phase == "idle"


def _row_status(rows: tuple[CapRow, ...], name: str) -> str:
    return next(row.status for row in rows if row.name == name)
