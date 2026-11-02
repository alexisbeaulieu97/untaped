"""What a provider call serves caches under: its directive, its stale lines and who answers.

``gather`` opens a :class:`CallState` for every provider call it makes
(:func:`call_state`): :meth:`Cache.serve <untaped.sdk.storage.cache.Cache.serve>`
reads its directive when its own is ``None``, and keeps the stale lines it
would say for whoever reads the answer. The contracts' answer cache serves
under :func:`answering`, so its lines name the provider and suggest no option.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped.sdk.diagnostic import Stale


@dataclass
class CallState:
    """One provider call's cache directive and what the call served.

    ``stale_lines`` are the stale lines the call's caches served: under a
    call state :meth:`Cache.serve` defers them to whoever reads the answer.
    ``caller`` names the provider call (``library's BookSource.books``) for an
    error raised from inside it.
    """

    refresh: bool | None
    caller: str = "a provider"
    stale: BaseException | None = None
    refreshed_at: datetime | None = None
    stale_lines: list[Stale] = field(default_factory=list)

    def served(self, at: datetime) -> None:
        if self.refreshed_at is None or at < self.refreshed_at:
            self.refreshed_at = at


_CALL: ContextVar[CallState | None] = ContextVar("untaped_contract_call", default=None)


@contextmanager
def call_state(refresh: bool | None, *, caller: str = CallState.caller) -> Iterator[CallState]:
    """Run a provider call under ``refresh``; yields what the call served."""
    state = CallState(refresh, caller=caller)
    token = _CALL.set(state)
    try:
        yield state
    finally:
        _CALL.reset(token)


def current_call() -> CallState | None:
    """The provider call an enclosing ``gather`` opened, else ``None``."""
    return _CALL.get()


def refresh_scope() -> bool | None:
    """The directive an enclosing ``gather`` opened, else ``None`` (refresh when stale)."""
    state = _CALL.get()
    return None if state is None else state.refresh


#: The answer cache being served (its qualified name) and the provider it answers for.
_ANSWERING: ContextVar[tuple[str, str] | None] = ContextVar("untaped_answering", default=None)


@contextmanager
def answering(cache: str, provider: str) -> Iterator[None]:
    """Serve the cache named ``cache`` (``<plugin>.<name>``) as the answer cache for ``provider``.

    Its lines name ``provider`` and suggest no option: whoever reads an
    answer has no ``--refresh``/``--cached`` of the answer cache's own.
    """
    token = _ANSWERING.set((cache, provider))
    try:
        yield
    finally:
        _ANSWERING.reset(token)


def answering_for(cache: str) -> str | None:
    """The provider the cache named ``cache`` answers for as the answer cache, else ``None``."""
    scope = _ANSWERING.get()
    return scope[1] if scope is not None and scope[0] == cache else None
