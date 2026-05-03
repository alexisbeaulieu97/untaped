"""Use case: fetch a single resource by name (with scope) or numeric id."""

from __future__ import annotations

from typing import Any

from untaped_awx.application.ports import ResourceClient
from untaped_awx.errors import ResourceNotFound
from untaped_awx.infrastructure.spec import AwxResourceSpec


class GetResource:
    def __init__(self, client: ResourceClient) -> None:
        self._client = client

    def __call__(
        self,
        spec: AwxResourceSpec,
        *,
        name: str | None = None,
        id: int | None = None,
        scope: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if id is not None:
            return self._client.get(spec, id).model_dump()
        if name is None:
            raise ValueError("GetResource requires either name= or id=")
        record = self._client.find_by_identity(spec, name=name, scope=scope)
        if record is None:
            raise ResourceNotFound(spec.kind, {"name": name, **(scope or {})})
        return record.model_dump()
