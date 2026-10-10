"""Pure repository inventory filter helpers."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from typing import Literal

from untaped_github.domain.models import GithubRepo

RepoMatcher = Callable[[GithubRepo], bool]
ArchivedMode = Literal["include", "exclude", "only"]
"""How a command treats archived repositories: keep them, drop them, or keep only them."""


def archived_allows(mode: ArchivedMode, archived: bool) -> bool:
    """Whether a repository with this ``archived`` flag passes ``mode``."""
    return mode == "include" or archived is (mode == "only")


def compile_repo_pattern(pattern: str, *, regex: bool = False) -> RepoMatcher:
    """Compile a case-insensitive matcher on the repo name, or ``owner/name`` with ``/``."""
    target = _target_getter(pattern)
    if regex:
        compiled = re.compile(pattern, re.IGNORECASE)
        return lambda row: compiled.search(target(row)) is not None
    glob = pattern.casefold()
    return lambda row: fnmatch.fnmatchcase(target(row).casefold(), glob)


def _target_getter(pattern: str) -> Callable[[GithubRepo], str]:
    if "/" in pattern:
        return lambda row: row.full_name
    return lambda row: row.full_name.rpartition("/")[2]
