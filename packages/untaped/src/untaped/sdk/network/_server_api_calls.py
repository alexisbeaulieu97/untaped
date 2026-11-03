"""What one port call sent: the requests a server API block's clients recorded during it.

Core builds every client an adapter talks through (``ApiAdapter.connect``)
and detection's own, and each records the requests it sends here
(:class:`~untaped.sdk.network.http._Sent`): an adapter's in the log of the
port call running now, of every port call around it, and of the
invocation; a detection's in its own log alone (:func:`recording_only`).
After a mismatch, the call's log says whether a write went through during
the failed call, which decides whether core may run the call again; the
invocation's log says whether anything took effect before it.
``ApiMismatch.watch`` reads the innermost call's last request to name what
the server was asked.
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


#: The logs recording now, innermost last: a port call's, inside any port call around it.
_CALL: ContextVar[tuple[CallLog, ...]] = ContextVar("untaped_server_api_call", default=())


def record(sent: _Sent) -> None:
    """Add ``sent`` to every log recording now; outside a port call, nothing keeps it."""
    for log in _CALL.get():
        log.sent.append(sent)


def last_sent() -> _Sent | None:
    """The last request the innermost port call sent; ``None`` outside one, or before any."""
    logs = _CALL.get()
    return logs[-1].sent[-1] if logs and logs[-1].sent else None


@contextmanager
def logging_into(*logs: CallLog) -> Iterator[CallLog]:
    """Record the requests sent inside the block into ``logs`` and every log already recording.

    A port call made inside another (an adapter calling another port, or a
    loop over one port's items calling another) records into its own log and
    into the enclosing call's, so no write goes unseen. Yields the last of ``logs``.
    """
    held = _CALL.get()
    token = _CALL.set((*held, *(log for log in logs if all(log is not each for each in held))))
    try:
        yield logs[-1]
    finally:
        _CALL.reset(token)


@contextmanager
def recording_only(log: CallLog) -> Iterator[CallLog]:
    """Record the requests sent inside the block into ``log`` alone (a detection's)."""
    token = _CALL.set((log,))
    try:
        yield log
    finally:
        _CALL.reset(token)
