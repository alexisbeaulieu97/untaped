"""An operation lock a plugin class declares: ``sync = Lock(wait=timedelta(minutes=10))``.

For an operation with no place of its own (a place has ``Dir.locked``): a
timer's ``sync`` and a manual ``apply`` must not interleave over the same
state. The lock is a file in the plugin's folder, ``<attribute>.lock``, held
across processes and re-entrant in one thread; ``locked()`` waits the
declared time, ``try_locked()`` tries once.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path

from untaped.sdk._block import OwnedBlock
from untaped.sdk.errors import ErrorCategory, UntapedError
from untaped.sdk.marks import experimental
from untaped.sdk.storage._fs import file_lock


@experimental
class Lock(OwnedBlock):
    """An operation lock: ``with self.plugin_class.sync.locked(): ...``.

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
        from untaped.sdk.run import current_run  # noqa: PLC0415 - the run imports the SDK

        return current_run().home / "plugins" / self.plugin / f"{self.name}.lock"

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
        path = self.path
        label = f"{self.plugin}.{self.name}"
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
            yield
