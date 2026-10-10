"""Credential helpers: the user's own, and untaped's in store worktrees whose path is gone."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from untaped.sdk import GitCommandError, run_git

_HELPER_KEY = re.compile(r"^credential\.(?:(?P<url>.+)\.)?helper$")
_UNTAPED_HELPER = re.compile(r"^\s*helper\s*=\s*(?P<value>!.*\bgit credential\s*)$")


def user_helpers(host: str, *, cwd: Path) -> list[str]:
    """The user's own credential helpers that apply to ``https://<host>``, in git's order.

    Read from the user's config alone (``cwd`` is a directory outside any
    repository); a helper scoped to another URL is left out.
    """
    try:
        result = run_git(
            ["config", "--global", "--get-regexp", r"^credential\..*helper$"],
            cwd=cwd,
            timeout=10,
            capture=True,
            check=False,
            ceiling=True,
            batch_ssh=False,
        )
    except GitCommandError:
        return []
    helpers = []
    for line in result.text.splitlines():
        key, _, value = line.partition(" ")
        match = _HELPER_KEY.match(key.lower())
        if match is None or not value:
            continue
        scope = match.group("url")
        if scope is None or scope.rstrip("/") in (f"https://{host}", host):
            helpers.append(value)
    return helpers


def missing_helper(root: Path) -> str | None:
    """An untaped helper path written into a store worktree config that no longer exists."""
    for config in sorted(root.glob("**/worktrees/*/config.worktree")):
        try:
            lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = _UNTAPED_HELPER.match(line)
            if match is None:
                continue
            try:
                words = shlex.split(match.group("value").strip().removeprefix("!"))
            except ValueError:
                continue
            if words and not Path(words[0]).exists():
                return words[0]
    return None
