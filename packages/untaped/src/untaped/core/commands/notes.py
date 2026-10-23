"""Printing the lines a config write returns (:data:`untaped.core.config.use_cases.Note`).

A write prints nothing itself, so a front decides when its lines show: the
``config`` and ``plugin`` commands at once, the setup screen once it closes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from untaped.runtime.sink import Diagnostics
from untaped.sdk.diagnostic import Failure

if TYPE_CHECKING:
    from untaped.core.config.use_cases import Note
    from untaped.runtime.render import MessageKind

_diag = Diagnostics()

_PRINTERS: dict[MessageKind, Callable[[str], object]] = {
    "success": _diag.success,
    "info": _diag.info,
    "warning": _diag.warning,
    "error": lambda text: _diag.failure(Failure.plain(text)),
}


def print_notes(notes: Iterable[Note]) -> None:
    """Print each note on stderr, in order, styled by its kind."""
    for kind, text in notes:
        _PRINTERS[kind](text)


__all__ = ["print_notes"]
