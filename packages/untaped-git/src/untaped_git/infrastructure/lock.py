"""The repo store's own per-repo lock: ``<repo>.git.lock``, reentrant in one process.

Writers take it; readers that only list do not. It is advisory: a user's own
git in a worktree never takes it, which is fine for refs (git's own ref locks)
and for gc (``gc.pruneExpire``). Lock order: a consumer that holds a lock of
its own (workspace's state lock) takes it first, so the repo lock is always
the inner lock. Nesting (``prefetched`` → ``prefetch``) re-enters the held
lock instead of opening the file again, which would block on itself.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from untaped.sdk import UntapedError, file_lock


@dataclass
class _Held:
    owner: threading.RLock = field(default_factory=threading.RLock)
    depth: int = 0
    stack: ExitStack = field(default_factory=ExitStack)


_GUARD = threading.Lock()
_HELD: dict[Path, _Held] = {}


@contextmanager
def repo_lock(
    repo: Path, *, timeout: float, error: Callable[[str], UntapedError]
) -> Iterator[None]:
    """Hold ``<repo>.lock`` for the block, re-entering it when this thread already does."""
    key = Path(f"{repo}.lock")
    with _GUARD:
        held = _HELD.setdefault(key, _Held())
    with held.owner:
        if held.depth == 0:
            try:
                key.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise error(f"could not lock repo store {repo}: {exc.strerror or exc}") from exc
            held.stack.enter_context(
                file_lock(
                    key,
                    timeout=timeout,
                    error=error,
                    busy=f"repo store is busy (another untaped process): {repo}",
                    failed=f"could not lock repo store {repo}",
                )
            )
        held.depth += 1
        try:
            yield
        finally:
            held.depth -= 1
            if held.depth == 0:
                held.stack.close()
