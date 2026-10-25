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
from typing import TYPE_CHECKING

from pydantic import Field

from untaped.core.commands.records import RegisteredHolder, RegisteredTarget, RegisterOutcome
from untaped.core.settings import CoreSettings
from untaped.sdk.fields import Output
from untaped.sdk.plugin import Command
from untaped.sdk.storage._refs import Reference, register_this_install, unregister

if TYPE_CHECKING:
    from untaped.sdk.run import Diag


class Register(Command[CoreSettings]):
    """Register this install, so cleanup keeps the store layouts it uses.

    Once per install: its later runs keep the registration current, and
    its complete fills remove the layouts no registered install uses.

    Examples:
        $ untaped setup register
        $ untaped setup register --remove
    """

    repairs = True
    remove: bool = Field(
        False,
        description="Unregister this install: other installs may then remove the layouts it used.",
    )
    output: Output[RegisterOutcome]

    def run(self) -> Iterator[RegisterOutcome]:
        # a list of one row, as 11.x printed it
        diag = self.ctx.diag
        yield _remove(diag) if self.remove else _register(diag)


def _register(diag: Diag) -> RegisterOutcome:
    ref, changed = register_this_install()
    held = ", ".join(target.describe() for target in ref.targets) or "no stores"
    if changed:
        diag.success(f"registered {ref.describe()} ({held})")
    else:
        diag.info(f"already registered: {ref.describe()} ({held})")
    return _outcome(ref, "registered" if changed else "unchanged")


def _remove(diag: Diag) -> RegisterOutcome:
    ref = unregister()
    if ref is None:
        diag.info("this install is not registered; nothing to remove")
        return RegisterOutcome(action="gone", environment="", version=None, targets=[])
    diag.success(f"unregistered {ref.describe()}: other installs may now remove its layouts")
    return _outcome(ref, "removed")


def _outcome(ref: Reference, action: str) -> RegisterOutcome:
    return RegisterOutcome(
        action=action,
        holder=None if ref.holder is None else RegisteredHolder(**ref.holder.model_dump()),
        environment=str(ref.anchor or ""),
        version=ref.version,
        targets=[RegisteredTarget(**target.model_dump()) for target in ref.targets],
    )


__all__ = ["Register"]
