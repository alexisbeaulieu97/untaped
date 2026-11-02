"""The one writer to stderr: one lock, one ``write()`` per line.

The sink (:mod:`untaped.runtime.sink`) and the progress stack
(:mod:`untaped.runtime.progress_stack`) write through it, so a progress
line and a diagnostic never interleave. A write from a thread already
inside the lock (a stream that logs, say) goes straight to the stream
instead of blocking on the lock that thread holds.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_WRITE_LOCK = threading.Lock()
_thread = threading.local()


@contextmanager
def locked() -> Iterator[None]:
    """Hold the write lock, marking this thread inside it; a no-op for a thread already inside."""
    if inside():
        yield
        return
    with _WRITE_LOCK:
        _thread.inside = True
        try:
            yield
        finally:
            _thread.inside = False


def inside() -> bool:
    """Whether this thread holds the write lock now."""
    return getattr(_thread, "inside", False)


def raw(text: str) -> None:
    """Write ``text`` to ``sys.stderr`` (read at each write) and flush it."""
    stream = sys.stderr
    stream.write(text)
    stream.flush()
