"""The installed git's version, and the floors the repo store depends on.

The floor is 2.29 (``fetch --stdin``, the ``noop`` negotiator and
``--no-write-fetch-head``, all used by the store's prefetch). From 2.46 git
honours ``GIT_NO_LAZY_FETCH``, so a blob read nobody prefetched fails loudly;
below it the prefetch's guard is the only check.
"""

from __future__ import annotations

import functools
import re
import shutil

from untaped.sdk import GitCommandError, run_git

#: The oldest git the repo store runs on.
FLOOR = (2, 29)
_VERSION = re.compile(r"git version (\d+)\.(\d+)(?:\.(\d+))?")


def git_version() -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` of the ``git`` on ``PATH``; ``None`` when absent or unreadable."""
    path = shutil.which("git")
    return None if path is None else _version_of(path)


def version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def floor_text() -> str:
    return ".".join(str(part) for part in FLOOR)


def below_floor(version: tuple[int, int, int] | None) -> bool:
    return version is not None and version[:2] < FLOOR


@functools.lru_cache(maxsize=8)
def _version_of(path: str) -> tuple[int, int, int] | None:
    try:
        result = run_git(["--version"], git=path, timeout=10, capture=True, batch_ssh=False)
    except GitCommandError:
        return None
    match = _VERSION.search(result.text)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)
