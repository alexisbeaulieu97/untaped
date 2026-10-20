"""``untaped setup register``: let cleanup keep this install's store layouts.

Writes this install's reference (:mod:`untaped.refs`): the directory holding
untaped's distribution, its version and the layout of every store the
composition declares. From then on each run of the install keeps the file
current, and the install's complete fills remove the layouts no registered
install holds. ``--remove`` deletes the reference. Nothing else ever
registers an install.
"""

from __future__ import annotations

from untaped.cli import emit
from untaped.management.records import RegisterOutcome
from untaped.output.sink import Diagnostics
from untaped.refs import Reference, register_this_install, unregister
from untaped.theme import OutputFormat

_diag = Diagnostics()


def run_register(*, remove: bool, fmt: OutputFormat, columns: list[str] | None) -> None:
    """Register this install, or with ``remove`` unregister it, and report it."""
    outcome = _remove() if remove else _register()
    emit([outcome], fmt=fmt, columns=columns)


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
        return RegisterOutcome(action="gone", holder="", environment="", version=None, targets=[])
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


__all__ = ["RegisterOutcome", "run_register"]
