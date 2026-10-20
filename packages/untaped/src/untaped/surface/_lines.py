"""The line: the unit of promise in ``surface.txt``, and the file's text form.

A file opens with ``# untaped surface <FORMAT>``, then one line per promise in
plain string order. A line is ``<area> <subject>[ <shape>]``, optionally
followed by two spaces and a mark: ``[experimental]``, ``[frozen]`` (never
removed or deprecated, not even in a major release), or
``[deprecated[, removed in <major>.0][; use <replacement>]]``. The mark is not
part of the line's identity: deprecating something in a minor, postponing its
removal or rewording its replacement keeps its promise. A line splits once on
its first ``  [`` (text, mark), and a mark once on its first ``; use `` (date,
replacement), so a replacement may hold ``;`` or ``]`` but never ``  [``.
:func:`upgrade` translates an older format's lines into this one, so a
baseline file written by an older generator compares cleanly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

#: The line format this generator reads. A PR that respells existing lines
#: bumps it and teaches :func:`upgrade` the old spelling. Format 2 added the
#: dated deprecation mark and format 3 the frozen mark; :func:`render` writes
#: the lowest format its marks need (format 1 for a file with neither), so an
#: older generator keeps reading what it can and refuses the rest plainly, and
#: an undated plugin's snapshot stays byte-for-byte what 11.0 wrote.
FORMAT = 3

HEADER = "# untaped surface"

_SEPARATOR = "  ["
_USE = "; use "
_DATED = re.compile(r"^deprecated, removed in (?P<major>[1-9][0-9]*)\.0$")


@dataclass(frozen=True)
class DeprecatedMark:
    """A deprecated line's mark: the major that removes it, and what to use instead.

    ``removed_in`` is ``None`` for an undated mark; ``use`` is the
    replacement as text, ``None`` when the mark names none.
    """

    removed_in: int | None = None
    use: str | None = None

    def __post_init__(self) -> None:
        use = self.use
        if use is not None and (not use or use != use.strip() or "\n" in use or _SEPARATOR in use):
            raise ValueError(
                f"a deprecated mark's replacement is one line without {_SEPARATOR!r}: {use!r}"
            )

    @property
    def dated(self) -> bool:
        """Whether the mark names its removal version, which only format 2 can write."""
        return self.removed_in is not None

    def __str__(self) -> str:
        text = "deprecated"
        if self.removed_in is not None:
            text += f", removed in {self.removed_in}.0"
        return text if self.use is None else f"{text}{_USE}{self.use}"


type LineMark = Literal["experimental", "frozen"] | DeprecatedMark
"""What a line's mark can be."""

DEPRECATED = DeprecatedMark()
"""The undated, replacement-less deprecated mark: format 1's ``[deprecated]``."""


@dataclass(frozen=True)
class Line:
    """One promise: its text (the identity) and its mark."""

    text: str
    mark: LineMark | None = None

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
        """The first word: ``cli``, ``cfg``, ``state``, ``sdk``, ``contract``, ``behaviour``…"""
        return self.text.partition(" ")[0]

    @property
    def deprecation(self) -> DeprecatedMark | None:
        """The line's deprecated mark, or ``None`` for a stable or experimental line."""
        return self.mark if isinstance(self.mark, DeprecatedMark) else None

    def __lt__(self, other: Line) -> bool:
        return str(self) < str(other)

    def __str__(self) -> str:
        return self.text if self.mark is None else f"{self.text}{_SEPARATOR}{self.mark}]"


def render(lines: Iterable[Line]) -> str:
    """The file text: the format header, then each distinct line in plain string order.

    The header names format 3 when a line is frozen, else format 2 when a
    deprecated mark carries a date. With neither the file is format 1, where
    every deprecated mark is ``[deprecated]`` (format 1 has no replacement),
    so an undated file reads as 11.0 wrote it.
    """
    distinct = set(lines)
    if any(line.mark == "frozen" for line in distinct):
        needed = 3
    elif any(line.deprecation is not None and line.deprecation.dated for line in distinct):
        needed = 2
    else:
        needed = 1
    if needed > 1:
        return "\n".join([f"{HEADER} {needed}", *sorted(str(line) for line in distinct)]) + "\n"
    body = sorted(
        {str(Line(line.text, DEPRECATED) if line.deprecation else line) for line in distinct}
    )
    return "\n".join([f"{HEADER} 1", *body]) + "\n"


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
    text, separator, rest = raw.partition(_SEPARATOR)
    if not separator:
        return Line(raw)
    if not rest.endswith("]"):
        raise ValueError(f"a surface line's mark ends with ']': {raw!r}")
    return Line(text, parse_mark(rest[:-1]))


def parse_mark(text: str) -> LineMark:
    """A mark as written between ``[`` and ``]``."""
    if text == "experimental":
        return "experimental"
    if text == "frozen":
        return "frozen"
    head, separator, use = text.partition(_USE)
    if head == "deprecated":
        return DeprecatedMark(None, use if separator else None)
    dated = _DATED.match(head)
    if dated is None:
        raise ValueError(f"not a surface mark: {text!r}")
    return DeprecatedMark(int(dated["major"]), use if separator else None)


def upgrade(lines: list[Line], from_format: int) -> list[Line]:
    """``lines`` written in ``from_format``, respelled in the current format.

    Format 1's marks are format 2's undated ones, and format 2's lines are
    format 3's, all spelled the same, so neither needs translation; each bump
    exists so an older generator refuses a newer mark plainly. A later format
    adds its step here, and a major release retires it.
    """
    if from_format in (1, 2, FORMAT):
        return lines
    raise ValueError(f"no upgrade from surface format {from_format} to {FORMAT}")
