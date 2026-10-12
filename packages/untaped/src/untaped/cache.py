"""Caches: data a plugin keeps from an upstream it can ask again.

``gather`` opens a call's cache directive here (:func:`call_state`) and a
cache reads it back (:func:`refresh_scope`): ``None`` refreshes what is past
its max age, ``True`` checks upstream now, ``False`` reads what is cached and
never checks.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CallState:
    """One provider call's cache directive and what the call served."""

    refresh: bool | None
    stale: BaseException | None = None
    refreshed_at: datetime | None = None

    def served(self, at: datetime) -> None:
        if self.refreshed_at is None or at < self.refreshed_at:
            self.refreshed_at = at


_CALL: ContextVar[CallState | None] = ContextVar("untaped_contract_call", default=None)


@contextmanager
def call_state(refresh: bool | None) -> Iterator[CallState]:
    """Run a provider call under ``refresh``; yields what the call served."""
    state = CallState(refresh)
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
