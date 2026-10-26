"""An operation lock a plugin class declares: ``sync = Lock(wait=timedelta(minutes=10))``.

For an operation with no place of its own (a place has ``Dir.locked``): a
timer's ``sync`` and a manual ``apply`` must not interleave over the same
state. The lock is a file in the plugin's folder, ``<attribute>.lock``, held
across processes and re-entrant in one thread; ``locked()`` waits the
declared time, ``try_locked()`` tries once.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path

from untaped.sdk._block import OwnedBlock
from untaped.sdk._home import PLUGINS_FOLDER
from untaped.sdk.errors import ErrorCategory, UntapedError
from untaped.sdk.marks import experimental
from untaped.sdk.storage._fs import file_lock


@experimental
class Lock(OwnedBlock):
    """An operation lock: ``with DotfilesPlugin.sync.locked(): ...``.

    ``wait`` is how long :meth:`locked` waits for another process holding it
    before it fails with ``<plugin>.<name> is busy (another untaped
    process)``. Taking it again in the same thread only counts.
    """

    block_kind = "lock"

    def __init__(self, *, wait: timedelta) -> None:
        if not isinstance(wait, timedelta) or wait < timedelta(0):
            raise TypeError(f"Lock: wait takes a timedelta of zero or more, not {wait!r}")
        super().__init__()
        self.wait = wait

    def __repr__(self) -> str:
        return f"Lock({self.where!r}, wait={self.wait!r})"

    @property
    def path(self) -> Path:
        """The lock file: ``plugins/<plugin>/<name>.lock`` under the home."""
        from untaped.sdk._run import current_run  # noqa: PLC0415 - the run imports the SDK

        return current_run().home / PLUGINS_FOLDER / self.plugin / f"{self.name}.lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the lock for the block, waiting up to ``wait`` for another process."""
        with self._held(self.wait.total_seconds()):
            yield

    @contextmanager
    def try_locked(self) -> Iterator[bool]:
        """Hold the lock for the block if no other process does: ``True`` when held."""
        with ExitStack() as stack:
            try:
                stack.enter_context(self._held(0))
            except UntapedError as exc:
                if exc.category != ErrorCategory.UNAVAILABLE:
                    raise
                held = False
            else:
                held = True
            yield held

    @contextmanager
    def _held(self, timeout: float) -> Iterator[None]:
        with operation_lock(self.plugin, f"{self.plugin}.{self.name}", self.path, timeout):
            yield


#: The operation locks this thread holds, outermost first: (plugin, label, path).
_HELD = threading.local()


@contextmanager
def operation_lock(plugin: str, label: str, path: Path, timeout: float) -> Iterator[None]:
    """Hold the operation lock file ``path`` of ``plugin``, waiting up to ``timeout`` seconds.

    The one implementation behind :class:`Lock` and ``Dir.locked``: held
    across processes (``<label> is busy (another untaped process)``),
    re-entrant in a thread, and in one order: a plugin's own lock before a
    provider's (a plugin whose distribution it requires directly), so taking
    a plugin's lock inside the lock of a plugin it requires fails, naming
    both. Taking again a lock the thread holds never waits, so it is never
    out of order.
    """
    held: list[tuple[str, str, Path]] = _HELD.__dict__.setdefault("stack", [])
    if not any(other_path == path for _, _, other_path in held):
        for other, other_label, _ in held:
            if other != plugin and _provides(other, plugin):
                raise UntapedError(
                    f"{label} is taken inside {other_label}: {plugin} requires {other}, and a "
                    "plugin's own lock comes before its provider's"
                )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UntapedError(f"could not lock {label}: {exc.strerror or exc}") from exc
    with file_lock(
        path,
        timeout=timeout,
        error=UntapedError,
        busy=f"{label} is busy (another untaped process)",
        failed=f"could not lock {label}",
    ):
        entry = (plugin, label, path)
        held.append(entry)
        try:
            yield
        finally:
            # remove this entry, not the last one: locks may be let go out of order
            for index in range(len(held) - 1, -1, -1):
                if held[index] is entry:
                    del held[index]
                    break


def _provides(provider: str, plugin: str) -> bool:
    """Whether ``plugin`` requires ``provider``'s distribution, as this install's rows say."""
    from untaped.sdk._run import current_run  # noqa: PLC0415 - the run imports the SDK

    rows = {row.name: row for row in current_run().composition.installed()}
    if provider not in rows or plugin not in rows:
        return False
    wanted = _canonical(rows[provider].distribution)
    return any(_canonical(each) == wanted for each in rows[plugin].requires)


def _canonical(distribution: str) -> str:
    return re.sub(r"[-_.]+", "-", distribution).lower()
