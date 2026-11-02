"""The setup screen's listing: the sections on the left, their rows and statuses, the forms.

Built from what :func:`~untaped.core.commands.setup_sections.read_listing`
read for a profile, when the screen opens, when the profile field loads
another, and after a write (:func:`_relisted`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from untaped.core.commands.setup_connection import connection_form
from untaped.core.commands.setup_screen_model import (
    INSTALL_ROW,
    NEW_PROFILE,
    CapRow,
    SetupModel,
    SetupRun,
)
from untaped.core.commands.setup_sections import (
    Listing,
    SectionPane,
    SettingRow,
    connection_row,
    section_mark,
)
from untaped.core.commands.setup_state import ServiceState, service_marks
from untaped.screen.components.form import Form
from untaped.screen.components.inputs import TextInput

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin


def section_order(
    services: Mapping[str, ComposedPlugin],
    sections: Sequence[str],
    plugins: frozenset[str],
    listing: Listing,
) -> tuple[str, ...]:
    """The sections on the left, fixed for the run.

    The services that still need something, the configured ones, core's
    sections, then the other plugins' alphabetically; a section with no
    row to show (only unset deprecated settings) is left out.
    """
    named = [name for name in services if name in sections]
    needs = [name for name in named if row_status(name, listing) != "configured"]
    configured = [name for name in named if name not in needs]
    others = [name for name in sections if name not in services and name in listing.rows]
    core = [name for name in others if name not in plugins]
    in_plugins = sorted(name for name in others if name in plugins)
    return (*needs, *configured, *core, *in_plugins)


def row_status(name: str, listing: Listing) -> str:
    state = listing.states.get(name)
    if state is not None:
        return _initial_status(state)
    return "invalid settings" if name in listing.errors else section_mark(name)


def _rows_of(run: SetupRun, name: str, listing: Listing) -> tuple[SettingRow, ...]:
    rows = listing.rows.get(name, ())
    state = listing.states.get(name)
    if state is None:
        return rows
    marks = service_marks(run.services[name])
    return (connection_row(state, marks), *rows)


def _panes(run: SetupRun, listing: Listing, profile: str) -> dict[str, SectionPane]:
    return {
        name: SectionPane(
            name, profile, _rows_of(run, name, listing), error=listing.errors.get(name, "")
        )
        for name in run.order
    }


def _forms(run: SetupRun, states: Mapping[str, ServiceState], profile: str) -> dict[str, Form]:
    return {
        name: connection_form(plugin, states[name], run.store)
        for name, plugin in run.services.items()
    }


def _relisted(run: SetupRun, model: SetupModel, listing: Listing) -> SetupModel:
    """``model`` over re-read ``listing``: rows, statuses and states; open fields stay."""
    rows = tuple(
        row if row.name == INSTALL_ROW else replace(row, status=_restatus(row, listing))
        for row in model.rows
    )
    panes = {
        name: pane.with_rows(_rows_of(run, name, listing), listing.errors.get(name, ""))
        for name, pane in model.panes.items()
    }
    return replace(
        model,
        rows=rows,
        states=listing.states,
        panes=panes,
        profile=_profile_field(model.profile, listing),
    )


def _restatus(row: CapRow, listing: Listing) -> str:
    """A row's status after a write: a service's stays, unless its validity changed."""
    state = listing.states.get(row.name)
    if state is None:
        return row_status(row.name, listing)
    if state.invalid is not None or row.status == "invalid settings":
        return _initial_status(state)
    return row.status


def _initial_status(state: ServiceState) -> str:
    """A service's status: every marked setting set, none, only its token missing, or a count."""
    if state.invalid is not None:
        return "invalid settings"
    if state.configured:
        return "configured"
    if len(state.missing) == state.required:
        return "not configured"
    if state.token_only:
        return "missing token"
    return f"{len(state.missing)} to set"


def _profile_field(field: TextInput, listing: Listing) -> TextInput:
    """The profile field saying, under it, whether its profile is still to be created."""
    help = "" if listing.exists else NEW_PROFILE
    return field if field.help == help else replace(field, help=help)
