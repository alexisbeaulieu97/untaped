"""What plugins were seen calling while a command ran: deprecated names the startup check can't see.

A name reached on an object a plugin got back (``store.prefetched(...).cat``)
is no import, so composition never finds it; its
:class:`~untaped.sdk.marks.UntapedDeprecationWarning` does, while the
command runs. :func:`note` takes each one the run's warning route shows,
maps the warning's file to its module and the module to the composed plugin
whose package it is in, and keeps the use once per command; a plugin
released with what it uses (``PluginDecl.lockstep``), core's own code and
code no plugin owns are skipped. :func:`flush`, when the command ends
(failed or not), adds what is new to core's ``run_uses`` rows, keyed by the
plugin's distribution and version, the name and the command. That write
never fails the command nor makes it wait: a home it can't write, a lock
another process holds or a damaged file drops the uses, and the next run
records them again. ``doctor`` lists the rows of installed plugin versions
as ``plugin-uses``; each write drops the rows of versions no longer installed.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field

from untaped.core.settings import NAME
from untaped.runtime.declaration import UseDecl
from untaped.sdk.fields import Key
from untaped.sdk.storage.state import StateRows

if TYPE_CHECKING:
    from untaped.runtime.registry import CompositionResult
    from untaped.sdk.marks import UntapedDeprecationWarning


class RunUse(BaseModel):
    """One deprecated name a plugin version was seen calling, and the command that called it."""

    model_config = ConfigDict(frozen=True)

    key: Annotated[str, Key] = Field(
        description="The plugin's distribution and version, the name's id and the command."
    )
    plugin: str
    distribution: str = Field(description="The calling plugin's distribution.")
    version: str = Field(description="The calling plugin's version.")
    id: str = Field(description="The name's surface identity: `sdk untaped_git.api.Store.cat`.")
    owner: str | None = Field(description="The distribution removing it; none for untaped's own.")
    removed_in: int | None = None
    replacement: str | None = None
    command: str = Field(description="The command that called it: `untaped acme report`.")

    def use(self) -> UseDecl:
        """This row as the use doctor's words read."""
        return UseDecl(
            id=self.id,
            how="calls",
            distribution=self.owner or "untaped",
            core=self.owner is None,
            removed_in=self.removed_in,
            replacement=self.replacement,
            where=self.command,
        )


#: Core's ``run_uses``: each deprecated name a plugin version was seen calling.
RUN_USES = StateRows[RunUse]()._bound_to(NAME, "run_uses")

#: Uses seen since the last command ended, by the calling plugin and the name's id.
_PENDING: dict[tuple[str, str], tuple[str, str, str, UntapedDeprecationWarning]] = {}
#: Row keys this process wrote or found written.
_WRITTEN: set[str] = set()
#: A module's file -> its name, for the modules imported when it was last filled.
_FILES: dict[str, str] = {}


def note(warning: UntapedDeprecationWarning, filename: str) -> None:
    """Keep ``warning``'s use when ``filename`` is a composed plugin's own code."""
    plugin = _plugin_of(filename)
    if plugin is None or warning.id is None:
        return
    name, distribution, version = plugin
    _PENDING.setdefault((name, warning.id), (name, distribution, version, warning))


def flush(command: str) -> None:
    """Add the uses ``command`` was seen calling to the rows; never raise, never wait."""
    from untaped.runtime.bootstrap import attached  # noqa: PLC0415 - bootstrap imports this lazily

    pending = list(_PENDING.values())
    _PENDING.clear()
    result = attached()
    if not pending or result is None:
        return
    rows = [_row(*each, command) for each in pending]
    new = [row for row in rows if row.key not in _WRITTEN]
    if not new:
        return
    installed = installed_versions(result)

    def add(current: list[RunUse]) -> list[RunUse]:
        kept = [row for row in current if (row.distribution, row.version) in installed]
        known = {row.key for row in kept}
        return [*kept, *(row for row in new if row.key not in known)]

    try:
        RUN_USES.replace_all(add, wait=timedelta(0))
    except Exception:  # a record never fails the command it records
        return
    _WRITTEN.update(row.key for row in new)


def installed_versions(result: CompositionResult) -> set[tuple[str, str]]:
    """Each discovered plugin's distribution and version: the rows that still describe code here."""
    return {(each.distribution, each.distribution_version) for each in result.candidates}


def recorded(result: CompositionResult) -> list[RunUse]:
    """The rows of plugin versions installed here; none when the rows can't be read."""
    installed = installed_versions(result)
    try:
        rows = RUN_USES.all()
    except Exception:  # a damaged record hides itself, never the doctor run
        return []
    return [row for row in rows if (row.distribution, row.version) in installed]


def forget() -> None:
    """Forget what this process saw and wrote (tests, embedding)."""
    _PENDING.clear()
    _WRITTEN.clear()
    _FILES.clear()


def _row(
    plugin: str,
    distribution: str,
    version: str,
    warning: UntapedDeprecationWarning,
    command: str,
) -> RunUse:
    identity = str(warning.id)
    return RunUse(
        key=f"{distribution} {version} {plugin} {identity} | {command}",
        plugin=plugin,
        distribution=distribution,
        version=version,
        id=identity,
        owner=warning.distribution,
        removed_in=warning.removed_in,
        replacement=warning.replacement,
        command=command,
    )


def _plugin_of(filename: str) -> tuple[str, str, str] | None:
    """The name, distribution and version of the composed plugin whose code ``filename`` is."""
    from untaped.runtime.bootstrap import attached  # noqa: PLC0415 - bootstrap imports this lazily

    result = attached()
    module = _module_of(filename)
    if result is None or module is None:
        return None
    declaration = result.mount.declaration
    name = declaration.owner_of(module)
    plugin = next((each for each in declaration.plugins if each.name == name), None)
    if plugin is None or plugin.lockstep or plugin.root:
        return None
    candidate = next((each for each in result.candidates if each.name == name), None)
    if candidate is None:
        return None
    return plugin.name, candidate.distribution, candidate.distribution_version


def _module_of(filename: str) -> str | None:
    """The imported module whose file ``filename`` is."""
    if filename not in _FILES:
        _FILES.clear()
        for name, module in list(sys.modules.items()):
            path = getattr(module, "__file__", None)
            if isinstance(path, str):
                _FILES[path] = name
    return _FILES.get(filename)
