"""The nonce untaped's credential helper line carries, so a casual call gets no secret.

The nonce is an HMAC-SHA256 of the worktree's real path under a secret kept
in the repo's own git directory (``untaped-helper.key``, 0600), so no
per-worktree record exists: ``untaped git credential get`` recomputes it for
the worktree git runs it in and answers only when the helper line's nonce
matches. It stops an agent or a script calling the helper by hand, not a
local process that reads ``.git/config`` and the key.

A moved worktree's real path changes, so its old helper answers nothing
until the owner rewrites it.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from untaped.sdk import run_git

KEY_FILE = "untaped-helper.key"
_LENGTH = 32


def helper_nonce(git_dir: Path, worktree: Path, *, create: bool = False) -> str | None:
    """The nonce for ``worktree`` of the repo at ``git_dir``; ``None`` without a key.

    ``create`` makes the repo's key the first time (the helper writer does).
    """
    key = _key(git_dir, create=create)
    if key is None:
        return None
    path = os.path.realpath(worktree).encode()
    return hmac.new(key, path, hashlib.sha256).hexdigest()[:_LENGTH]


def nonce_matches(nonce: str, cwd: Path) -> tuple[bool, Path | None]:
    """Whether ``nonce`` is the one for the worktree around ``cwd``, and that worktree."""
    result = run_git(
        ["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],
        cwd=cwd,
        capture=True,
        check=False,
        timeout=10,
    )
    lines = result.text.splitlines()
    if result.returncode != 0 or len(lines) != 2:
        return False, None
    worktree, git_dir = Path(lines[0]), Path(lines[1])
    expected = helper_nonce(git_dir, worktree)
    return expected is not None and hmac.compare_digest(expected, nonce), worktree


def _key(git_dir: Path, *, create: bool) -> bytes | None:
    path = git_dir / KEY_FILE
    found = _read(path)
    if found is not None or not create:
        return found
    secret = secrets.token_bytes(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read(path)  # another writer made it first
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secret.hex() + "\n")
    return secret


def _read(path: Path) -> bytes | None:
    try:
        return bytes.fromhex(path.read_text(encoding="utf-8").strip())
    except OSError, ValueError:
        return None
