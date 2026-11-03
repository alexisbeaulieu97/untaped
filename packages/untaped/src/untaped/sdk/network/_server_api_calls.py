"""What one port call sent: the requests a server API block's clients recorded during it.

Core builds every client an adapter talks through (``ApiAdapter.connect``)
and detection's own, and each records the requests it sends here
(:class:`~untaped.sdk.network.http._Sent`), in the log of the port call
running now. After a mismatch, the log says whether a write went through
during the failed call, which decides whether core may run the call again;
``ApiMismatch.watch`` reads its last request to name what the server was
asked.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.sdk.network.http import _Sent


@dataclass
class CallLog:
    """The requests one port call sent, in order (threads it started add to it too)."""

    sent: list[_Sent] = field(default_factory=list)

    @property
    def wrote(self) -> bool:
        """Whether a write went through during the call, or may have."""
        return any(each.wrote for each in self.sent)


_CALL: ContextVar[CallLog | None] = ContextVar("untaped_server_api_call", default=None)


def record(sent: _Sent) -> None:
    """Add ``sent`` to the port call running now; outside one, nothing keeps it."""
    log = _CALL.get()
    if log is not None:
        log.sent.append(sent)


def last_sent() -> _Sent | None:
    """The last request the port call running now sent; ``None`` outside one, or before any."""
    log = _CALL.get()
    return log.sent[-1] if log is not None and log.sent else None


@contextmanager
def logging_into(log: CallLog) -> Iterator[CallLog]:
    """Record the requests sent inside the block (and the threads it starts) into ``log``."""
    token = _CALL.set(log)
    try:
        yield log
    finally:
        _CALL.reset(token)
