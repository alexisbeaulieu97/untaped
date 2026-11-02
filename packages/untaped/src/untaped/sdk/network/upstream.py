"""How hard a call to an upstream tries: an HTTP request or a networked ``git`` command.

Outside any block, a call has its full timeout and retries. Inside
:func:`quick_probe` every call is capped at :data:`PROBE_TIMEOUT` and never
retried (``doctor --online``). Inside :func:`fail_fast` the same holds until
one call succeeds; from then on every call has its full timeout and retries
again. An automatic cache refresh runs under it: offline, each call fails
inside ``PROBE_TIMEOUT`` and the cached data is served; reachable, the fill
is not cut short.

The budget is one shared cell, so the context a worker thread copies
(``bounded_map``) sees the same flag: one success lifts the cap for every
worker of the block. A call to an upstream reads it (:func:`capped`: the
longest timeout it may take, and no retries) and reports a success
(:func:`succeeded`): :class:`untaped.sdk.network.HttpClient` and untaped-git's
``run_git`` do. :func:`full_retries` steps out of
an enclosing :func:`fail_fast` for a fill that must not be cut short.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from untaped.sdk.marks import experimental

#: Longest timeout (seconds) of a capped call.
PROBE_TIMEOUT = 10.0


class _Budget:
    """A block's cap: lifted for good by the first success when ``until_success``."""

    def __init__(self, *, until_success: bool) -> None:
        self.until_success = until_success
        # An Event, not a bool: the copies of a context share this object, not a value.
        self._lifted = threading.Event()

    def capped(self) -> bool:
        return not self._lifted.is_set()

    def succeeded(self) -> None:
        if self.until_success:
            self._lifted.set()


_BUDGET: ContextVar[_Budget | None] = ContextVar("untaped_upstream_budget", default=None)


@contextmanager
def _budget(budget: _Budget | None) -> Iterator[None]:
    token = _BUDGET.set(budget)
    try:
        yield
    finally:
        _BUDGET.reset(token)


@contextmanager
def quick_probe() -> Iterator[None]:
    """Fail fast inside the block: no retries, timeouts capped at :data:`PROBE_TIMEOUT`.

    ``doctor --online`` wraps each service probe in it, so an unreachable
    service costs one short attempt instead of several full-length ones.
    """
    with _budget(_Budget(until_success=False)):
        yield


@contextmanager
def fail_fast() -> Iterator[None]:
    """Cap every call as :func:`quick_probe` does until one succeeds, then cap none.

    The budget of an automatic refresh: offline it gives up inside
    :data:`PROBE_TIMEOUT`; once upstream answered, a slow or flaky call gets
    its full timeout and retries.
    """
    with _budget(_Budget(until_success=True)):
        yield


@contextmanager
def full_retries() -> Iterator[None]:
    """Give every call its full timeout and retries, leaving an enclosing :func:`fail_fast`.

    A ``--refresh`` or a first fill nested in another cache's automatic
    refresh: the outer budget is not this fill's, and this fill's success
    does not lift it. Inside :func:`quick_probe` every call stays capped.
    """
    outer = _BUDGET.get()
    with _budget(outer if outer is not None and not outer.until_success else None):
        yield


@experimental
def capped() -> float | None:
    """The longest timeout (seconds) a call starting now may take, or ``None``: not capped.

    A capped call is also tried once, never retried.

    First consumer: core's ``HttpClient``; then untaped-git's ``run_git``.
    """
    budget = _BUDGET.get()
    return PROBE_TIMEOUT if budget is not None and budget.capped() else None


@experimental
def succeeded() -> None:
    """Report that a call reached upstream and succeeded (lifts a :func:`fail_fast` cap).

    First consumer: core's ``HttpClient``; then untaped-git's ``run_git``.
    """
    budget = _BUDGET.get()
    if budget is not None:
        budget.succeeded()
