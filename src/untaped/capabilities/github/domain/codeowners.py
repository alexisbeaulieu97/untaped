"""Pure CODEOWNERS parsing and owner lookup."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

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
    if pattern == "*":
        return True
    rooted = pattern.startswith("/")
    normalized_pattern = pattern.lstrip("/")
    if normalized_pattern.endswith("/"):
        directory = normalized_pattern.rstrip("/")
        if rooted:
            return path == directory or path.startswith(f"{directory}/")
        return path == directory or path.startswith(f"{directory}/") or f"/{directory}/" in path
    if rooted or "/" in normalized_pattern:
        return fnmatch.fnmatchcase(path, normalized_pattern)
    return fnmatch.fnmatchcase(path.rsplit("/", maxsplit=1)[-1], normalized_pattern)
