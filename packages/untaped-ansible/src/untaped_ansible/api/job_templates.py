from __future__ import annotations

from typing import Any

from .base import TowerApiClient


class JobTemplatesApi:
    """API wrapper for Ansible Tower job templates."""

    def __init__(self, client: TowerApiClient) -> None:
        self._client = client

    def list(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        response = self._client.get("/api/v2/job_templates/", params=params)
        payload = response.json()
        return list(payload.get("results", [])) if isinstance(payload, dict) else []

    def get(self, template_id: int | str) -> dict[str, Any]:
        response = self._client.get(f"/api/v2/job_templates/{template_id}/")
        return response.json()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(
            "/api/v2/job_templates/",
            json=payload,
            expected_status=201,
        )
        return response.json()

    def update(self, template_id: int | str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.patch(
            f"/api/v2/job_templates/{template_id}/",
            json=payload,
            expected_status=200,
        )
        return response.json()

    def delete(self, template_id: int | str) -> bool:
        self._client.delete(
            f"/api/v2/job_templates/{template_id}/",
            expected_status=(200, 202, 204),
        )
        return True
