"""The port the controller connection satisfies for ``awx ping``.

Each feature keeps its own ports beside its use cases
(:mod:`~untaped_awx.application.resources.ports`,
:mod:`~untaped_awx.application.jobs.ports`,
:mod:`~untaped_awx.application.suites.ports`); this one is the client's own.
"""

from __future__ import annotations

from typing import Any, Protocol


class AwxPingService(Protocol):
    """Returns the raw AAP ``/ping/`` payload; :class:`Ping` validates
    the shape into a :class:`PingStatus` entity."""

    def ping(self) -> dict[str, Any]: ...

    def me(self) -> dict[str, Any]:
        """Authenticated ``/me/`` list payload; fails when the token is rejected."""
        ...


__all__ = ["AwxPingService"]
