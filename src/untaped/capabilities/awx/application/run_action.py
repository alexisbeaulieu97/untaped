"""Submit a declared action once and normalize its explicitly declared execution kind.

The name-based adapter is retained for the AWX test runner; interactive bulk
commands resolve once and call execute with each fixed target ID.
"""

from __future__ import annotations

from typing import Any

from untaped.capabilities.awx.application.get_resource import parse_resource_id
from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.domain import ActionPayload, Job, ResourceSpec
from untaped.capabilities.awx.errors import AwxApiError, ResourceNotFound


class RunAction:
    def __init__(self, client: ResourceClient) -> None:
        self._client = client

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        action: str,
        scope: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        by_id: bool = False,
    ) -> Job:
        if by_id:
            record_id = parse_resource_id(name)
        else:
            record = self._client.find_by_identity(spec, name=name, scope=scope)
            if record is None:
                raise ResourceNotFound(spec.kind, {"name": name, **(scope or {})})
            record_id = record.id
        return self.execute(spec, record_id, action=action, payload=payload)

    def execute(
        self,
        spec: ResourceSpec,
        record_id: int,
        *,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> Job:
        """Submit once to an already selected ID; never repeat identity lookup."""
        action_spec = next((a for a in spec.actions if a.name == action), None)
        if action_spec is None:
            raise AwxApiError(f"{spec.kind} has no action {action!r}")
        if action_spec.path is None or action_spec.returns == "none":
            raise AwxApiError(f"{spec.kind}.{action} requires fixed execution targets")
        action_payload = ActionPayload(**payload) if payload else None
        result = self._client.action(spec, record_id, action_spec.path, payload=action_payload)
        # The declared result kind is authoritative even when the controller
        # omits type. A conflicting type indicates an invalid action response.
        if result.get("type") not in (None, action_spec.returns):
            raise AwxApiError(f"{spec.kind}.{action} returned an unexpected execution kind")
        return Job.model_validate({**result, "kind": action_spec.returns})
