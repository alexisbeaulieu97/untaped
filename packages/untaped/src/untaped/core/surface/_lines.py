"""The line: the unit of promise in ``surface.txt``, and the file's text form.

A file opens with ``# untaped surface <FORMAT>``, then one line per promise in
plain string order. A line is ``<area> <subject>[ <shape>]``, optionally
followed by two spaces and a mark: ``[experimental]`` or
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

from untaped.sdk.diagnostic import KIND_PATTERN

#: The line format this generator reads and writes. A PR that respells
#: existing lines bumps it and teaches :func:`upgrade` the old spelling.
#: Format 2 added the dated deprecation mark. Format 3 gives each optional
#: parameter of a contract method a line of its own and writes a declared
#: contract's methods only as ``contract`` lines, not again as ``sdk`` ones.
FORMAT = 3

HEADER = "# untaped surface"

_SEPARATOR = "  ["
_PLUGIN_FACING = frozenset({"sdk", "contract", "convention"})
_SUBJECT = re.compile(r"^(?:sdk|contract) [\w.]+")
_SHAPE_WORD = re.compile(r"^ (implement=|abstract=|extra=)")
_PARAMETER = re.compile(
    r"^(?:sdk|contract) (?P<callable>[\w.]+)\("
    r"(?:(?P<name>\w+)(?:@\d+/?)?=|(?P<variadic>\*{1,2}\w+))\)"
)
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

    def __str__(self) -> str:
        text = "deprecated"
        if self.removed_in is not None:
            text += f", removed in {self.removed_in}.0"
        return text if self.use is None else f"{text}{_USE}{self.use}"


type LineMark = Literal["experimental"] | DeprecatedMark
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
    def plugin_facing(self) -> bool:
        """Whether plugins build on this line, so it lasts two majors after deprecation.

        The ``sdk`` (the SDK, ``untaped.core.api`` and each plugin's ``api`` and
        ``testing`` modules), ``contract`` and ``convention`` areas, and a record
        kind's own name (``record github.repo``, in the kind grammar), which
        users write into their files; a kind's fields and the models nested in
        it (``record jira.IssueLink``) keep one major. The only list of these;
        every other line keeps the one-major rule.
        """
        if self.area in _PLUGIN_FACING:
            return True
        return self.area == "record" and bool(KIND_PATTERN.match(self.text.removeprefix("record ")))

    @property
    def key(self) -> str:
        """The line's name without its shape: what a respelling in place keeps.

        A parameter line keeps ``<area> <callable>(<name>=)``; an ``implement=``,
        ``abstract=`` or ``extra=`` line its subject and that word; any other
        ``sdk`` or ``contract`` line its subject alone, so an attribute that
        became a method, a respelled signature or a retyped field keeps its key.
        A ``choice=`` or ``base=`` line names one value each, so its key is its
        whole text, as is every line of another area.
        """
        parameter = self.parameter
        if parameter is not None:
            variadic = parameter[1].startswith("*")
            return self.text if variadic else f"{self.area} {parameter[0]}({parameter[1]}=)"
        found = _SUBJECT.match(self.text)
        if found is None:
            return self.text
        rest = self.text[found.end() :]
        if rest.startswith((" choice=", " base=")):
            return self.text
        word = _SHAPE_WORD.match(rest)
        return f"{found[0]} {word[1]}" if word else found[0]

    @property
    def parameter(self) -> tuple[str, str] | None:
        """``(callable, name)`` for a line that names one parameter; ``None`` otherwise.

        An optional parameter's line (``sdk X.f(name=)``, ``name@2=``, ``name@2/=``,
        ``contract o.c.m(name@1=)``) and a variadic one's (``sdk X.f(*args)``).
        """
        found = _PARAMETER.match(self.text)
        return None if found is None else (found["callable"], found["name"] or found["variadic"])

    @property
    def deprecation(self) -> DeprecatedMark | None:
        """The line's deprecated mark, or ``None`` for a stable or experimental line."""
        return self.mark if isinstance(self.mark, DeprecatedMark) else None

    def __lt__(self, other: Line) -> bool:
        return str(self) < str(other)

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
    head, separator, use = text.partition(_USE)
    if head == "deprecated":
        return DeprecatedMark(None, use if separator else None)
    dated = _DATED.match(head)
    if dated is None:
        raise ValueError(f"not a surface mark: {text!r}")
    return DeprecatedMark(int(dated["major"]), use if separator else None)


def upgrade(lines: list[Line], from_format: int) -> list[Line]:
    """``lines`` written in ``from_format``, respelled in the current format.

    Format 1's marks are format 2's undated ones, spelled the same. Format 3
    splits each optional parameter off a contract method's line and drops the
    ``sdk`` method lines of a declared contract (a class whose bases name
    ``Contract`` and not ``Provider``), which its ``contract`` lines carry. A
    later format adds its step here, and a major release retires it.
    """
    if from_format == FORMAT:
        return lines
    if from_format not in (1, 2):
        raise ValueError(f"no upgrade from surface format {from_format} to {FORMAT}")
    declared = _declared_contracts(lines)
    upgraded: list[Line] = []
    for line in lines:
        method = _SDK_METHOD.match(line.text)
        if method is not None and method["cls"] in declared:
            continue
        upgraded.extend(_split_optional(line))
    return upgraded


_CONTRACT_BASE = "base=untaped.sdk.contracts.Contract"
_PROVIDER_BASE = "base=untaped.sdk.contracts.Provider"
_SDK_METHOD = re.compile(r"^sdk (?P<cls>[\w.]+)\.\w+\(")
_CONTRACT_METHOD = re.compile(
    r"^contract (?P<subject>[\w.]+)\((?P<params>.*)\) -> (?P<returns>.+)$"
)


def _declared_contracts(lines: list[Line]) -> set[str]:
    """The classes whose ``sdk`` lines say they declare a contract, from format 2 lines."""
    bases: dict[str, set[str]] = {}
    for line in lines:
        subject, _, shape = line.text.removeprefix("sdk ").partition(" ")
        if line.area == "sdk" and shape.startswith("base="):
            bases.setdefault(subject, set()).add(shape)
    return {
        cls
        for cls, found in bases.items()
        if _CONTRACT_BASE in found and _PROVIDER_BASE not in found
    }


def _split_optional(line: Line) -> list[Line]:
    """A format-2 contract method line, as format 3 spells it: a line per optional parameter."""
    found = _CONTRACT_METHOD.match(line.text)
    if found is None:
        return [line]
    required: list[str] = []
    optional: list[Line] = []
    star = False
    position = 0
    for part in _parameters(found["params"]):
        if part == "*":
            star = True
            continue
        name, _, rest = part.partition(": ")
        token, has_default, default = rest.partition(" = ")
        if not star:
            position += 1
        if has_default:
            spelled = name if star else f"{name}@{position}"
            subject = f"{found['subject']}({spelled}=)"
            optional.append(Line(f"contract {subject} {token} default={default}", line.mark))
            continue
        if star and "*" not in required:
            required.append("*")
        required.append(part)
    text = f"contract {found['subject']}({', '.join(required)}) -> {found['returns']}"
    return [Line(text, line.mark), *optional]


_NEXT_PARAMETER = re.compile(r", (?=\w+: |\*(?:, |$))")


def _parameters(params: str) -> list[str]:
    """A format-2 parameter list's parts: ``name: type[ = default]`` or ``*``.

    A part ends at a ``, `` that starts another one; a JSON-quoted default may
    hold ``, `` or brackets, so quoted text is skipped.
    """
    parts: list[str] = []
    start = index = 0
    while index < len(params):
        char = params[index]
        if char == '"':
            index += 1
            while index < len(params) and params[index] != '"':
                index += 2 if params[index] == "\\" else 1
        elif _NEXT_PARAMETER.match(params, index):
            parts.append(params[start:index])
            start = index + 2
            index += 1
        index += 1
    tail = params[start:]
    return [*parts, tail] if tail else parts
