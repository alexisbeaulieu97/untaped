"""A plugin's planned write to its own settings: :meth:`PluginHandle.plan_settings` returns one.

Core owns config writes; a plugin reaches the same writer ``config set``
uses through its handle, and gets a plan back: checked before anything is
written, previewed through ``Confirm(preview=)`` or ``DryRun``, then written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from untaped.sdk._run import PluginHandle
from untaped.sdk.marks import experimental

if TYPE_CHECKING:
    from untaped.sdk._output import PreviewItem
    from untaped.sdk.records import OutcomeRecord


@experimental
@dataclass(frozen=True)
class SettingsWritten:
    """What :meth:`SettingsPlan.write` wrote.

    ``outcomes`` are core's ``untaped.setting_outcome`` rows, one per change
    in the order the plan took them, each naming the profile it landed in.
    ``target`` is the profile ``config set`` writes this run (``default``
    is the base); a ``HomeOnly`` key lands in the base whatever it is.

    First consumer: untaped-dotfiles' package choices (#1085).
    """

    outcomes: tuple[OutcomeRecord, ...]
    target: str


@experimental
class SettingsPlan(Protocol):
    """Changes to a plugin's own settings, checked and not yet written.

    Each change was validated with the others against ``config.yml`` when the
    plan was made, so a bad value fails before any file is touched.

    First consumer: untaped-dotfiles' package choices (#1085).
    """

    def preview(self) -> list[PreviewItem]:
        """One line per change, as ``Confirm(preview=)`` or ``DryRun.go_ahead`` prints it."""
        ...

    def write(self) -> SettingsWritten:
        """Write every change as one, under core's lock, comments kept.

        The changes are validated again against the file as it is now, so a
        file changed since the plan can still refuse them (nothing is written
        then). A key this run still reads from a picked profile or a project's
        file gets core's "this run still reads ..." line.
        """
        ...


# Marked beside the plan it returns: importing the run pays for no marks. Every way to
# PluginHandle (``untaped.sdk``'s table) imports this module too.
experimental(PluginHandle.plan_settings)

__all__ = ["SettingsPlan", "SettingsWritten"]
