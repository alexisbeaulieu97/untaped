"""The guard: which promised lines a new surface no longer keeps.

Two set operations and one relaxation. Experimental lines of either file are
dropped (they promise nothing), and every remaining old line whose identity is
missing from the new file's is a removal, unless :func:`relaxed` finds that the
change only loosened a caller-shaped owner line, or :func:`respelled` finds the
promise kept under a new spelling: a setting renamed (its old key still read,
``cfg <key> -> <new key>``, even when that key is renamed again), or a switch
that also takes its negative (a ``bool | None`` directive whose unset value is
the old default).

A caller-shaped owner (a command, or a function, constructor or method of a
class plugins only call) may drop required items: its required positionals
may shrink to a prefix and its required keyword items to a subset, as long as
every dropped item comes back as an optional line of the same subject, at its
old position or under its name. ``NAME`` becoming ``NAME...`` still takes one
value, so it counts as the same positional; the reverse does not. An
implementer-shaped owner (a ``contract`` line, or an ``sdk`` class plugins
implement, written ``implement=``) is never relaxed: a provider's signature
must match exactly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from untaped.core.surface._lines import Line

_CLI_WORD = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CLI_POSITIONAL = re.compile(r"^(?P<name>[A-Z][A-Z0-9_]*)(?P<many>\.\.\.)?$")
_CLI_OPTIONAL_POSITIONAL = re.compile(
    r"^\[(?P<name>[A-Z][A-Z0-9_]*)(?P<many>\.\.\.)?\]@(?P<at>\d+)$"
)
_SDK_OWNER = re.compile(
    r"^sdk (?P<subject>[\w.]+)\((?P<params>[^)]*)\)(?P<callbacks>(?: callback\(\w+, [\w*]+\))*)$"
)
_SDK_CALLBACK = re.compile(r"callback\((?P<name>\w+), (?P<arity>[\w*]+)\)")
_SDK_OPTIONAL = re.compile(
    r"^sdk (?P<subject>[\w.]+)\((?P<name>\w+)(?:@(?P<at>\d+)(?P<only>/)?)?=\) default=.*?"
    r"(?: callback\((?P<arity>[\w*]+)\))?$"
)
_SDK_IMPLEMENTER = re.compile(r"^sdk (?P<subject>[\w.]+) implement=")


@dataclass(frozen=True)
class _Positional:
    name: str
    many: bool = False
    only: bool = False
    """Positional-only (``sdk``): a keyword caller can't use it."""


@dataclass(frozen=True)
class _Owner:
    subject: str
    positionals: tuple[_Positional, ...]
    keywords: dict[str, bool]
    """Required keyword item → whether it is repeatable (``--opt VALUE...``)."""
    callbacks: dict[str, str]
    """Required callback parameter → its arity."""


def removed(old: Sequence[Line], new: Sequence[Line]) -> list[Line]:
    """The stable lines of ``old`` that ``new`` neither keeps nor relaxes, in file order.

    Only ``new``'s lines that still promise count: marking a stable line
    experimental removes its promise as surely as deleting it.
    """
    kept = [line for line in new if line.mark != "experimental"]
    present = {line.identity for line in kept}
    implementers = _implementers([*old, *new])
    gone = [line for line in old if line.mark != "experimental" and line.identity not in present]
    return sorted(
        line
        for line in gone
        if not relaxed(line, kept, implementers=implementers) and not respelled(line, present)
    )


def respelled(line: Line, present: set[str]) -> bool:
    """Whether the gone ``line`` keeps its promise under a new spelling ``present`` holds.

    A setting renamed in a minor stays read under its old key until the next
    major (``cfg <key> -> <new key>``), with the new key keeping each of the
    old one's lines, and renamed again it points where its target went (the
    end of the chain); a switch that became tri-state is
    still the same flag (``<switch line> default=unset``).
    """
    if line.area == "cfg":
        key = line.text.split(" ")[1]
        prefix = f"cfg {key} -> "
        if line.text.startswith(prefix):
            # a renamed key renamed again: still read, now pointing where its target went
            target = line.text.removeprefix(prefix)
            return any(
                text.startswith(prefix)
                and f"cfg {target} -> {text.removeprefix(prefix)}" in present
                for text in present
            )
        # the new key keeps the old one's shape: a rename, never a retyping
        return any(
            text.startswith(prefix)
            and line.text.replace(f"cfg {key} ", f"cfg {text.removeprefix(prefix)} ", 1) in present
            for text in present
        )
    return line.text.endswith(" switch") and f"{line.text} default=unset" in present


def _implementers(lines: Iterable[Line]) -> frozenset[str]:
    found = set()
    for line in lines:
        match = _SDK_IMPLEMENTER.match(line.text)
        if match:
            found.add(match["subject"])
    return frozenset(found)


def relaxed(line: Line, new: Sequence[Line], *, implementers: frozenset[str] = frozenset()) -> bool:
    """Whether the gone ``line`` is a caller-shaped owner that ``new`` only loosened (D5)."""
    old = _owner(line.text)
    if old is None:
        return False
    if line.area == "sdk" and (
        old.subject in implementers or old.subject.rpartition(".")[0] in implementers
    ):
        return False
    texts = [each.text for each in new]
    current = next(
        (
            owner
            for owner in (_owner(text) for text in texts)
            if owner is not None and owner.subject == old.subject
        ),
        None,
    )
    if current is None:
        return False
    cli = line.area == "cli"
    return (
        _narrowed(current, old, cli=cli)
        and _same_callbacks(current, old)
        and _dropped_reappear(current, old, _optional_lines(texts, old.subject, cli=cli))
    )


def _narrowed(current: _Owner, old: _Owner, *, cli: bool) -> bool:
    """Condition 1: positionals a prefix of the old ones, keyword items a subset."""
    if not _prefix(current.positionals, old.positionals, cli=cli):
        return False
    return all(
        name in old.keywords and (many or not old.keywords[name])
        for name, many in current.keywords.items()
    )


def _same_callbacks(current: _Owner, old: _Owner) -> bool:
    """A callback kept as required keeps its arity, and no new one appears."""
    for name, arity in old.callbacks.items():
        kept = name in current.keywords or any(p.name == name for p in current.positionals)
        if kept and current.callbacks.get(name) != arity:
            return False
    return all(old.callbacks.get(name) == arity for name, arity in current.callbacks.items())


def _dropped_reappear(current: _Owner, old: _Owner, optional: list[_Optional]) -> bool:
    """Condition 2: every dropped item is an optional line at its position or under its name."""
    start = len(current.positionals)
    for index, positional in enumerate(old.positionals[start:]):
        at = start + index + 1
        if not _reappears(
            optional, positional.name, at, positional.many, old.callbacks, only=positional.only
        ):
            return False
    return all(
        name in current.keywords or _reappears(optional, name, None, many, old.callbacks)
        for name, many in old.keywords.items()
    )


def _prefix(new: tuple[_Positional, ...], old: tuple[_Positional, ...], *, cli: bool) -> bool:
    if len(new) > len(old):
        return False
    for now, was in zip(new, old, strict=False):
        if now.name != was.name or (was.many and not now.many and cli):
            return False
        if now.only and not was.only:
            return False
    return True


@dataclass(frozen=True)
class _Optional:
    name: str
    at: int | None
    many: bool
    arity: str | None
    only: bool = False
    """Positional-only (``sdk`` ``name@k/=``): a keyword caller can't use it."""


def _optional_lines(texts: Iterable[str], subject: str, *, cli: bool) -> list[_Optional]:
    found: list[_Optional] = []
    for text in texts:
        if cli:
            words = text.split(" ")[1:]
            head = subject.split(" ")
            if words[: len(head)] != head or len(words) <= len(head):
                continue
            item, *rest = words[len(head) :]
            match = _CLI_OPTIONAL_POSITIONAL.match(item)
            if match:
                found.append(_Optional(match["name"], int(match["at"]), bool(match["many"]), None))
            elif item.startswith("--") and rest and rest[0] in ("VALUE", "VALUE..."):
                # A required option comes back valued: as a switch, ``--opt x`` breaks.
                found.append(_Optional(item, None, rest[0] == "VALUE...", None))
        else:
            match = _SDK_OPTIONAL.match(text)
            if match and match["subject"] == subject:
                at = int(match["at"]) if match["at"] else None
                only = bool(match["only"])
                found.append(_Optional(match["name"], at, False, match["arity"], only))
    return found


def _reappears(
    optional: list[_Optional],
    name: str,
    at: int | None,
    many: bool,
    callbacks: dict[str, str],
    *,
    only: bool = False,
) -> bool:
    for each in optional:
        if each.name != name or (many and not each.many) or (each.only and not only):
            continue
        if at is not None and each.at != at:
            continue
        if callbacks.get(name) != each.arity:
            continue
        return True
    return False


def _owner(text: str) -> _Owner | None:
    """The owner line ``text`` describes, or ``None`` when it is any other line."""
    if text.startswith("cli "):
        return _cli_owner(text)
    if text.startswith("sdk "):
        return _sdk_owner(text)
    return None


def _cli_owner(text: str) -> _Owner | None:
    words = text.split(" ")[1:]
    subject: list[str] = []
    while words and _CLI_WORD.match(words[0]):
        subject.append(words.pop(0))
    if not subject:
        return None
    positionals: list[_Positional] = []
    keywords: dict[str, bool] = {}
    while words:
        word = words.pop(0)
        positional = _CLI_POSITIONAL.match(word)
        if positional and not keywords:
            positionals.append(_Positional(positional["name"], bool(positional["many"])))
        elif word.startswith("--") and words and words[0] in ("VALUE", "VALUE..."):
            keywords[word] = words.pop(0) == "VALUE..."
        else:
            return None
    return _Owner(" ".join(subject), tuple(positionals), keywords, {})


def _sdk_owner(text: str) -> _Owner | None:
    match = _SDK_OWNER.match(text)
    if match is None:
        return None
    params = [item for item in match["params"].split(", ") if item]
    if any(not re.match(r"^(\w+|/|\*)$", item) for item in params):
        return None
    positionals: list[_Positional] = []
    keywords: dict[str, bool] = {}
    keyword = False
    for item in params:
        if item == "/":
            positionals = [_Positional(p.name, only=True) for p in positionals]
        elif item == "*":
            keyword = True
        elif keyword:
            keywords[item] = False
        else:
            positionals.append(_Positional(item))
    callbacks = {m["name"]: m["arity"] for m in _SDK_CALLBACK.finditer(match["callbacks"])}
    return _Owner(match["subject"], tuple(positionals), keywords, callbacks)
