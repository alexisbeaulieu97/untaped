"""The ``untaped setup`` screen: how it is built, its keys and its view.

Its model and run are in :mod:`~untaped.core.commands.setup_screen_model`;
:func:`~untaped.core.commands.setup_screen_navigation.update` sends each
message to the module of its concern (navigation, rows, save, listing).

Two panes under a profile field. The left lists this install, then every
settings section: the services first (those that still need something above
the configured ones, each with a status), then core's section and the other
plugins' (each with its plugin's stability mark, or ``invalid settings``); the
order is fixed when the screen opens, so a saved service does not jump. The
right shows the selected section's settings
(:mod:`untaped.core.commands.setup_sections`): enter opens a row's field and
writes it as ``config set`` does, ``delete`` unsets it. What the section
names, the screen does not know: rows come from the config use cases, fields
from each setting's shape.

A service's first row is its connection: enter opens its form (its marked
settings, a token source as tabs, buttons;
:mod:`untaped.core.commands.setup_connection`).
Saving it checks before it writes: the form's values become a
:class:`Candidate`, the plugin's online checks run against them inside a
settings overlay (nothing is stored yet,
:func:`untaped.core.doctor_rows.online_probe_rows`), and only a pass runs the
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
and in no frame, repr or message of this module's own. The model never holds a
secret setting's value (rows show it masked).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from functools import cache
from typing import TYPE_CHECKING

from rich.console import Group, RenderableType
from rich.text import Text

from untaped.core.commands.setup_screen_listing import (
    _forms,
    _panes,
    _profile_field,
    row_status,
    section_order,
)
from untaped.core.commands.setup_screen_model import (
    INSTALL_ROW,
    CapRow,
    SetupModel,
    SetupResult,
    SetupRun,
    _registrable,
)
from untaped.core.commands.setup_screen_navigation import update
from untaped.core.commands.setup_screen_save import _install_form, _Note
from untaped.core.commands.setup_sections import CONNECTION, Listing, SectionPane, read_listing
from untaped.runtime.registry import CompositionResult, contract_owners
from untaped.runtime.settings import resolve_config_path
from untaped.screen.components.choices import ListItem, SingleList
from untaped.screen.components.draw import role_style
from untaped.screen.components.form import Form
from untaped.screen.components.inputs import COMPLETION_ROWS, TextInput
from untaped.screen.components.layout import Panes
from untaped.screen.components.lists import ListEditor, MapEditor
from untaped.screen.core import Binding, Cmd, Frame, Screen
from untaped.sdk.network._token_store import TokenStore

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin

__all__ = ["SetupModel", "SetupResult", "setup_screen"]

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


def setup_screen(
    result: CompositionResult,
    services: Mapping[str, ComposedPlugin],
    *,
    sections: Sequence[str],
    profile: str,
    store: TokenStore | None,
    profiles: Sequence[str] = (),
    command: str,
    alternative: str,
) -> Screen[SetupModel, SetupResult]:
    """The setup screen for ``sections`` (``services`` among them) in ``profile``.

    ``store`` is the token store picked once for the run (``None`` when none
    works or no service takes a command); ``profiles`` the names the profile
    field completes; ``command`` and ``alternative`` the command lines that
    open the screen and do its work without one. The profile's listing is
    read here, so the sections' order is fixed before the screen opens; a
    profile that can't be read raises its ``ConfigError`` before anything is
    drawn.
    """
    services = dict(services)
    plugins = frozenset(plugin.name for plugin in result.plugins if not plugin.root)
    listing = read_listing(profile, services)
    run = SetupRun(
        result=result,
        services=services,
        sections=tuple(sections),
        store=store,
        profiles=tuple(profiles),
        profile=profile,
        plugins=plugins,
        owners=contract_owners(result),
        # Tested once, just before the first token goes to the store.
        preflight=cache(store.preflight) if store is not None else _no_preflight,
        order=section_order(services, sections, plugins, listing),
    )
    return Screen(
        init=lambda: init(run, listing),
        update=lambda model, message: update(run, model, message),
        view=view,
        title="Set up untaped",
        command=command,
        alternative=alternative,
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
    if isinstance(pane.editing, ListEditor | MapEditor):
        return _list_enter(pane.editing, changed=pane.to_write() != pane.initial)
    if pane.editing is not None:
        return "write"
    row = pane.row
    if row is None or pane.confirm:
        return None
    if not row.key:
        return "open"
    if row.ranking is not None and not row.ranking.method:
        return "add"
    return "edit" if row.descriptor is not None else None


def _list_enter(editor: ListEditor | MapEditor, *, changed: bool) -> str:
    """Enter in a list or map editor: keep the open value, put the moved item down, or act on
    the row.

    On ``done`` it writes, or just closes the editor when nothing changed.
    """
    if editor.editing is not None:
        return "keep"
    if editor.cursor == editor.rows - 1:
        return "write" if changed else "close"
    if isinstance(editor, MapEditor):
        return "edit"
    if editor.moving:
        return "put down"
    return "edit" if editor.cursor < len(editor.items) else "add"


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


#: What the shared keys do here, for the footer and the help overlay.
_SHARED_LABELS: Mapping[str, str | Callable[[SetupModel], str | None]] = {
    "tab": _tab_label,
    "enter": _enter_label,
    "ctrl-s": _save_label,
}


# --- init and view --------------------------------------------------------


def init(run: SetupRun, listing: Listing) -> tuple[SetupModel, list[Cmd]]:
    """The screen as it opens, on ``listing`` (what ``run.profile`` showed when it was built)."""
    from untaped.sdk.storage._refs import install_state  # noqa: PLC0415 - off the startup path

    field = TextInput(
        "Profile",
        run.profile,
        complete=lambda text: [name for name in run.profiles if name.startswith(text)],
    )
    rows = (
        CapRow(INSTALL_ROW, install_state()),
        *(CapRow(name, row_status(name, listing)) for name in run.order),
    )
    model = SetupModel(
        profile=_profile_field(field, listing),
        current=run.profile,
        rows=rows,
        states=listing.states,
        forms={INSTALL_ROW: _install_form(), **_forms(run, listing.states, run.profile)},
        opened=listing.states,
        panes=_panes(run, listing, run.profile),
        config_path=str(resolve_config_path()),
        selected=1 if run.order else 0,
    )
    return model, []


def view(model: SetupModel, frame: Frame) -> RenderableType:
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
        tuple(_item(frame, row) for row in model.rows),
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


def _item(frame: Frame, row: CapRow) -> ListItem:
    label = row.status + frame.ellipsis() if row.status in _PROGRESS else row.status
    return ListItem(
        row.name,
        row.name,
        detail=label,
        detail_role=_STATUS_ROLES.get(row.status, "screen.accent"),
    )


def _field_rows(frame: Frame, field: TextInput, *, focused: bool) -> int:
    """The lines the profile field takes: its box (or label and value) and a note under it.

    While it is focused and completing, the candidates count too, under a rule when boxed.
    """
    boxed = frame.box() is not None
    rows = (3 if boxed else 2) + (1 if field.error or field.help else 0)
    if focused and field.completing:
        rows += min(COMPLETION_ROWS, len(field.matches)) + (1 if boxed else 0)
    return rows


def _no_preflight() -> None:
    """Nothing to test: no store was picked."""
