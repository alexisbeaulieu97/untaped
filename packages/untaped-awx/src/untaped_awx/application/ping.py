"""Use case: report AAP control plane health."""

from __future__ import annotations

from typing import Any, Protocol

from untaped_awx.domain import PingStatus


class AwxPingService(Protocol):
    def ping(self) -> dict[str, Any]: ...


class Ping:
    """Validates the AAP ``/ping/`` payload into a domain entity."""

    def __init__(self, client: AwxPingService) -> None:
        self._client = client

    def __call__(self) -> PingStatus:
        return PingStatus.model_validate(self._client.ping())
