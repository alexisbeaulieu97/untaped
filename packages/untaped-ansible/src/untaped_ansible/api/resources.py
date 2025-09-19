from __future__ import annotations

from typing import Any

from httpx import QueryParams

from .base import TowerApiClient
from .errors import TowerApiError


class TowerResourcesApi:
    """Helpers for resolving Tower resource identifiers."""

    def __init__(self, client: TowerApiClient) -> None:
        self._client = client

    def get_inventory_id(self, name: str) -> int:
        response = self._client.get(self._with_name_query("/api/v2/inventories/", name))
        return self._extract_first_id(response.json(), resource="inventory", name=name)

    def get_project_id(self, name: str) -> int:
        response = self._client.get(self._with_name_query("/api/v2/projects/", name))
        return self._extract_first_id(response.json(), resource="project", name=name)

    def get_credential_id(self, name: str) -> int:
        response = self._client.get(self._with_name_query("/api/v2/credentials/", name))
        return self._extract_first_id(response.json(), resource="credential", name=name)

    @staticmethod
    def _extract_first_id(payload: Any, *, resource: str, name: str) -> int:
        if not isinstance(payload, dict):
            raise TowerApiError(
                f"Unexpected response while looking up {resource} '{name}'", payload=payload
            )
        results = payload.get("results", [])
        if not results:
            raise TowerApiError(f"{resource.title()} '{name}' not found")
        first = results[0]
        if not isinstance(first, dict) or "id" not in first:
            raise TowerApiError(
                f"Invalid response payload while resolving {resource} '{name}'",
                payload=first,
            )
        return int(first["id"])

    @staticmethod
    def _with_name_query(path: str, name: str) -> str:
        query = QueryParams({"name": name})
        separator = "&" if "?" in path else "?"
        return f"{path.rstrip('?')}{separator}{query}"
