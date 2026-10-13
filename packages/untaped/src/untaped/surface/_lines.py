"""The line: the unit of promise in ``surface.txt``, and the file's text form.

A file opens with ``# untaped surface <FORMAT>``, then one line per promise in
plain string order. A line is ``<area> <subject>[ <shape>]``, optionally
followed by two spaces and a mark (``[experimental]`` or ``[deprecated]``).
The mark is not part of the line's identity: deprecating something in a minor
keeps its promise. :func:`upgrade` translates an older format's lines into
this one, so a baseline file written by an older generator compares cleanly.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

#: The line format this generator writes. A PR that respells existing lines
#: bumps it and teaches :func:`upgrade` the old spelling.
FORMAT = 1

HEADER = "# untaped surface"

type MarkName = Literal["experimental", "deprecated"]

_MARKS: tuple[MarkName, ...] = ("experimental", "deprecated")
_SEPARATOR = "  ["


@dataclass(frozen=True, order=True)
class Line:
    """One promise: its text (the identity) and its mark."""

    text: str
    mark: MarkName | None = None

    def __post_init__(self) -> None:
        if not self.text or self.text != self.text.strip() or "\n" in self.text:
            raise ValueError(f"a surface line is one non-blank line of text, not {self.text!r}")
        if _SEPARATOR in self.text:
            raise ValueError(f"a surface line's text never holds {_SEPARATOR!r}: {self.text!r}")

    @property
    def identity(self) -> str:
        """The line without its mark: what a later release must keep."""
        return self.text

    @property
    def area(self) -> str:
        """The first word: ``cli``, ``cfg``, ``state``, ``sdk``, ``contract`` or ``convention``."""
        return self.text.partition(" ")[0]

    def __str__(self) -> str:
        return self.text if self.mark is None else f"{self.text}{_SEPARATOR}{self.mark}]"


def render(lines: Iterable[Line]) -> str:
    """The file text: the format header, then each distinct line in plain string order."""
    body = sorted({str(line) for line in lines})
    return "\n".join([f"{HEADER} {FORMAT}", *body]) + "\n"


def parse(text: str) -> list[Line]:
    """The lines of a file, upgraded to the current :data:`FORMAT`.

    Raises :class:`ValueError` for a file without the header, or one written
    by a newer generator.
    """
    head, _, body = text.partition("\n")
    prefix = f"{HEADER} "
    if not head.startswith(prefix) or not head[len(prefix) :].isdigit():
        raise ValueError(f"not a surface file: its first line is {head!r}, not '{HEADER} N'")
    version = int(head[len(prefix) :])
    if version > FORMAT:
        raise ValueError(
            f"surface format {version} is newer than this generator's ({FORMAT}); "
            "upgrade untaped to read it"
        )
    return upgrade([_parse_line(raw) for raw in body.splitlines() if raw.strip()], version)


def _parse_line(raw: str) -> Line:
    for mark in _MARKS:
        suffix = f"{_SEPARATOR}{mark}]"
        if raw.endswith(suffix):
            return Line(raw[: -len(suffix)], mark)
    return Line(raw)


def upgrade(lines: list[Line], from_format: int) -> list[Line]:
    """``lines`` written in ``from_format``, respelled in the current format.

    Format 1 is the first, so there is nothing to translate yet. A later
    format adds its step here and retires it at the next major.
    """
    if from_format == FORMAT:
        return lines
    raise ValueError(f"no upgrade from surface format {from_format} to {FORMAT}")
