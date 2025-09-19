from __future__ import annotations

from typing import Any

from ..api.resources import TowerResourcesApi


class ResourceValidationService:
    """Resolve Tower resource identifiers before making API calls."""

    def __init__(self, resources_api: TowerResourcesApi) -> None:
        self._resources_api = resources_api

    def ensure_inventory(self, inventory: str | int) -> int:
        return self._ensure_identifier(inventory, self._resources_api.get_inventory_id)

    def ensure_project(self, project: str | int) -> int:
        return self._ensure_identifier(project, self._resources_api.get_project_id)

    def ensure_credential(self, credential: str | int) -> int:
        return self._ensure_identifier(credential, self._resources_api.get_credential_id)

    def resolve_job_template_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a copy with inventory/project/credentials resolved to IDs."""

        resolved = dict(payload)
        resolved["inventory"] = self.ensure_inventory(payload["inventory"])
        resolved["project"] = self.ensure_project(payload["project"])

        credentials = payload.get("credentials") or []
        resolved["credentials"] = [self.ensure_credential(cred) for cred in credentials]
        return resolved

    @staticmethod
    def _ensure_identifier(value: str | int, resolver) -> int:
        if isinstance(value, int):
            return value
        return int(resolver(value))
