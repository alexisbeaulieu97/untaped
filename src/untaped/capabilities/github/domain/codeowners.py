"""Pure CODEOWNERS parsing and owner lookup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

CODEOWNERS_LOCATIONS: tuple[str, ...] = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


@dataclass(frozen=True)
class _Rule:
    pattern: str
    owners: tuple[str, ...]


@dataclass(frozen=True)
class CodeownersRules:
    """Parsed CODEOWNERS rules using last-match-wins lookup."""

    rules: tuple[_Rule, ...]

    def owners_for(self, path: str) -> tuple[str, ...]:
        normalized = path.strip("/")
        owners: tuple[str, ...] = ()
        for rule in self.rules:
            if _matches(rule.pattern, normalized):
                owners = rule.owners
        return owners

    def default_owners(self) -> tuple[str, ...]:
        owners: tuple[str, ...] = ()
        for rule in self.rules:
            if rule.pattern == "*":
                owners = rule.owners
        return owners


def parse_codeowners(text: str) -> CodeownersRules:
    """Parse CODEOWNERS text, skipping comments, blanks, and unsupported lines."""
    rules: list[_Rule] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        pattern = fields[0]
        if not _supported_pattern(pattern):
            continue
        rules.append(_Rule(pattern=pattern, owners=tuple(fields[1:])))
    return CodeownersRules(tuple(rules))


def _supported_pattern(pattern: str) -> bool:
    return (
        bool(pattern) and not pattern.startswith("!") and "[" not in pattern and "]" not in pattern
    )


def _matches(pattern: str, path: str) -> bool:
    """Match ``path`` with GitHub's gitignore-style CODEOWNERS semantics.

    A pattern matching a directory owns everything beneath it, except a
    pattern whose last segment is a bare ``*`` (``docs/*``), which GitHub
    documents as owning direct children only. A trailing ``/`` restricts
    the pattern to directories.
    """
    regex, directory_only, propagates = _compile(pattern)
    parts = path.split("/")
    ancestors = ["/".join(parts[:end]) for end in range(1, len(parts))]
    if not directory_only and regex.fullmatch(path):
        return True
    if not propagates:
        return False
    return any(regex.fullmatch(ancestor) for ancestor in ancestors)


@cache
def _compile(pattern: str) -> tuple[re.Pattern[str], bool, bool]:
    directory_only = pattern.endswith("/")
    body = pattern.rstrip("/")
    # Any slash other than a trailing one anchors the pattern to the repo root.
    anchored = "/" in body
    body = body.lstrip("/")
    segments = body.split("/")
    propagates = directory_only or segments[-1] != "*"

    out: list[str] = [] if anchored else ["(?:.*/)?"]
    last = len(segments) - 1
    for index, segment in enumerate(segments):
        if segment == "**":
            if index == last:
                out.append(".*" if index == 0 else "/.*")
            elif index == 0:
                out.append("(?:.*/)?")
            else:
                out.append("/(?:.*/)?")
            continue
        if index > 0 and segments[index - 1] != "**":
            out.append("/")
        out.append(_segment_regex(segment))
    return re.compile("".join(out)), directory_only, propagates


def _segment_regex(segment: str) -> str:
    out: list[str] = []
    chars = iter(segment)
    for char in chars:
        if char == "\\":
            out.append(re.escape(next(chars, "\\")))
        elif char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
    return "".join(out)
