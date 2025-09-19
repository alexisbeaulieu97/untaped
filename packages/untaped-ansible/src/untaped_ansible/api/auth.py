from __future__ import annotations

from typing import Any

from .base import TowerApiClient
from .errors import TowerAuthenticationError


class TowerAuthApi:
    """Authentication helper for obtaining Tower tokens."""

    def __init__(self, client: TowerApiClient) -> None:
        self._client = client

    def login(self, *, username: str, password: str) -> dict[str, Any]:
        response = self._client.post(
            "/api/v2/authtoken/",
            json={"username": username, "password": password},
            expected_status=200,
            use_auth=False,
        )

        data = response.json()
        if not isinstance(data, dict):  # pragma: no cover
            raise TowerAuthenticationError("Unexpected authentication response", response=response)

        token = data.get("token")
        if isinstance(token, str):
            self._client.set_token(token)

        return data
