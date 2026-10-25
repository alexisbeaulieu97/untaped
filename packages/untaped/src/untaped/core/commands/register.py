"""``untaped setup register``: let cleanup keep this install's store layouts.

Writes this install's reference (:mod:`untaped.sdk.storage._refs`): the directory holding
untaped's distribution, its version and the layout of every store the
composition declares. From then on each run of the install keeps the file
current, and the install's complete fills remove the layouts no registered
install holds. ``--remove`` deletes the reference. Nothing else ever
registers an install.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.core.commands.records import RegisterOutcome
from untaped.core.settings import CoreSettings
from untaped.runtime.sink import Diagnostics
from untaped.sdk.fields import Output
from untaped.sdk.marks import experimental
from untaped.sdk.plugin import Command
from untaped.sdk.storage._refs import Reference, register_this_install, unregister

_diag = Diagnostics()


class Register(Command[CoreSettings]):
    """Register this install, so cleanup keeps the store layouts it uses.

    Once per install: its later runs keep the registration current, and
    its complete fills remove the layouts no registered install uses.

    Examples:
        $ untaped setup register
        $ untaped setup register --remove
    """

    # its record names the experimental RefHolder and RefTarget
    stability = experimental
    repairs = True
    remove: bool = Field(
        False,
        description="Unregister this install: other installs may then remove the layouts it used.",
    )
    output: Output[RegisterOutcome]

    def run(self) -> Iterator[RegisterOutcome]:
        # a list of one row, as 11.x printed it
        yield _remove() if self.remove else _register()


def _register() -> RegisterOutcome:
    ref, changed = register_this_install()
    held = ", ".join(target.describe() for target in ref.targets) or "no stores"
    if changed:
        _diag.success(f"registered {ref.describe()} ({held})")
    else:
        _diag.info(f"already registered: {ref.describe()} ({held})")
    return _outcome(ref, "registered" if changed else "unchanged")


def _remove() -> RegisterOutcome:
    ref = unregister()
    if ref is None:
        _diag.info("this install is not registered; nothing to remove")
        return RegisterOutcome(action="gone", environment="", version=None, targets=[])
    _diag.success(f"unregistered {ref.describe()}: other installs may now remove its layouts")
    return _outcome(ref, "removed")


def _outcome(ref: Reference, action: str) -> RegisterOutcome:
    return RegisterOutcome(
        action=action,
        holder=ref.holder,
        environment=str(ref.anchor or ""),
        version=ref.version,
        targets=list(ref.targets),
    )


__all__ = ["Register"]
