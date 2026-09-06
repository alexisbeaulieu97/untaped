"""Pure repository inventory filter helpers."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable

from untaped.capabilities.github.domain.models import RepoListResult

RepoMatcher = Callable[[RepoListResult], bool]


def compile_repo_pattern(pattern: str, *, regex: bool = False) -> RepoMatcher:
    """Compile a case-insensitive repo name/full_name matcher."""
    target = _target_getter(pattern)
    if regex:
        compiled = re.compile(pattern, re.IGNORECASE)
        return lambda row: compiled.search(target(row)) is not None
    glob = pattern.casefold()
    return lambda row: fnmatch.fnmatchcase(target(row).casefold(), glob)


def _target_getter(pattern: str) -> Callable[[RepoListResult], str]:
    if "/" in pattern:
        return lambda row: row.full_name
    return lambda row: row.name
