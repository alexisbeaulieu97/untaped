"""Use case: report AAP control plane health and the authenticated user."""

from __future__ import annotations

from untaped.capabilities.awx.application.ports import AwxPingService
from untaped.capabilities.awx.domain import PingStatus


class Ping:
    """Validates ``/ping/`` and proves the token through ``/me/``.

    ``/ping/`` is unauthenticated, so on its own it reports healthy even
    with a bad token; the ``/me/`` call fails (401) in that case.
    """

    def __init__(self, client: AwxPingService) -> None:
        self._client = client

    def __call__(self) -> PingStatus:
        payload = self._client.ping()
        results = self._client.me().get("results") or []
        user = results[0].get("username") if results and isinstance(results[0], dict) else None
        return PingStatus.model_validate({**payload, "user": user})
