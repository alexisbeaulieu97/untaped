"""Submit a declared action once and normalize its explicitly declared execution kind.

The name-based adapter is retained for the AWX test runner; interactive bulk
commands resolve once and call execute with each fixed target ID.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from untaped.capabilities.awx.application.get_resource import parse_resource_id
from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.domain import ActionPayload, Job, ResourceSpec
from untaped.capabilities.awx.errors import ActionResponseError, AwxApiError, ResourceNotFound


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
        if action_spec.path is None or not action_spec.returns:
            raise AwxApiError(f"{spec.kind}.{action} requires fixed execution targets")
        action_payload = ActionPayload(**payload) if payload else None
        result = self._client.action(spec, record_id, action_spec.path, payload=action_payload)
        raw_id = result.get("id")
        execution_id = raw_id if type(raw_id) is int and raw_id > 0 else None
        kind = result.get("type")
        if kind is None and len(action_spec.returns) == 1:
            kind = next(iter(action_spec.returns))
        if not isinstance(kind, str) or kind not in action_spec.returns:
            raise ActionResponseError(
                f"{spec.kind}.{action}: submitted execution has missing or unexpected kind",
                execution_id=execution_id,
                execution_kind=None,
            )
        try:
            if execution_id is None:
                raise ValueError("missing positive integer execution ID")
            return Job.model_validate({**result, "kind": kind})
        except (ValidationError, ValueError) as exc:
            raise ActionResponseError(
                f"{spec.kind}.{action}: submitted execution response is invalid",
                execution_id=execution_id,
                execution_kind=kind,
            ) from exc
