"""``untaped setup register``: let cleanup keep this install's store layouts.

Writes this install's reference (:mod:`untaped.refs`): the directory holding
untaped's distribution, its version and the layout of every store the
composition declares. From then on each run of the install keeps the file
current, and the install's complete fills remove the layouts no registered
install holds. ``--remove`` deletes the reference. Nothing else ever
registers an install.
"""

from __future__ import annotations

from typing import ClassVar

from untaped.cli import emit
from untaped.messages import shown_path
from untaped.output.sink import Diagnostics
from untaped.records import OutcomeRecord
from untaped.refs import Reference, composed_targets, register, this_install, unregister
from untaped.theme import OutputFormat

_diag = Diagnostics()


class RegisterOutcome(OutcomeRecord, kind="untaped.register_outcome"):
    """What ``setup register`` did: ``registered``, ``unchanged``, ``removed`` or ``gone``.

    ``environment`` is the directory holding untaped's distribution,
    ``layouts`` the ``<plugin>.<store>@<layout>`` targets the reference names.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("action", "version", "environment", "layouts")

    holder: str
    environment: str
    version: str | None
    layouts: list[str]


def run_register(*, remove: bool, fmt: OutputFormat, columns: list[str] | None) -> None:
    """Register this install, or with ``remove`` unregister it, and report it."""
    outcome = _remove() if remove else _register()
    emit([outcome], fmt=fmt, columns=columns)


def _register() -> RegisterOutcome:
    targets = composed_targets() or ()
    before = this_install()
    ref = register(targets)
    same = before is not None and (before.version, before.targets) == (ref.version, ref.targets)
    held = ", ".join(ref.targets) or "no stores"
    if same:
        _diag.info(f"already registered: {_describe(ref)} ({held})")
    else:
        _diag.success(f"registered {_describe(ref)} ({held})")
    return _outcome(ref, "unchanged" if same else "registered")


def _remove() -> RegisterOutcome:
    ref = unregister()
    if ref is None:
        _diag.info("this install is not registered; nothing to remove")
        return RegisterOutcome(action="gone", holder="", environment="", version=None, layouts=[])
    _diag.success(f"unregistered {_describe(ref)}: other installs may now remove its layouts")
    return _outcome(ref, "removed")


def _describe(ref: Reference) -> str:
    where = shown_path(ref.anchor) if ref.anchor is not None else "?"
    return f"untaped {ref.version or '?'} at {where}"


def _outcome(ref: Reference, action: str) -> RegisterOutcome:
    return RegisterOutcome(
        action=action,
        holder=ref.holder,
        environment=str(ref.anchor or ""),
        version=ref.version,
        layouts=list(ref.targets),
    )


__all__ = ["RegisterOutcome", "run_register"]
