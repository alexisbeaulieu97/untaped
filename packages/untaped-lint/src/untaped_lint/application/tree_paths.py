"""Which files a repository's tree holds: what git tracks or would track, else every file.

Inside a checkout the tree is ``git ls-files --cached --others
--exclude-standard``: tracked files plus untracked ones git doesn't ignore,
so a new file is linted before ``git add``. Outside one (or with no git) it
is every file under the root, ``.git`` aside. Files listed but deleted from
the working tree are left out.
"""

from __future__ import annotations

import os
from pathlib import Path

from untaped.sdk import GitCommandError, run_git

_TIMEOUT = 60.0


def tree_paths(root: Path) -> list[str]:
    """The files under ``root``, relative and ``/``-separated, sorted."""
    listed = _git_paths(root)
    if listed is None:
        listed = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        ]
    return sorted(path for path in set(listed) if (root / path).is_file())


def _git_paths(root: Path) -> list[str] | None:
    try:
        result = run_git(
            ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            timeout=_TIMEOUT,
            capture=True,
        )
    except GitCommandError:
        return None  # not a checkout, or no git: every file under the root
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]
