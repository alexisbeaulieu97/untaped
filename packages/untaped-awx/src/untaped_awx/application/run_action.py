"""Use case: trigger a custom AWX action (launch / project update).

Both ``JobTemplate.launch`` and ``Project.update`` POST against
``<api_path>/<id>/<action>/`` and return an async execution record. We
normalise that record into a :class:`Job` so downstream watching /
logging works the same regardless of action.
"""

from __future__ import annotations

from typing import Any

from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import Job, ResourceSpec
from untaped_awx.errors import AwxApiError, ResourceNotFound

_KIND_OF_ACTION_RESULT: dict[str, str] = {
    "launch": "job",
    "update": "project_update",
}


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
    ) -> Job:
        action_spec = next((a for a in spec.actions if a.name == action), None)
        if action_spec is None:
            raise AwxApiError(
                f"{spec.kind} has no action {action!r} "
                f"(available: {[a.name for a in spec.actions]})"
            )
        params: dict[str, str] = {"name": name}
        for k, v in (scope or {}).items():
            params[f"{k}__name"] = v
        record = self._client.find(spec, params=params)
        if record is None:
            raise ResourceNotFound(spec.kind, {"name": name, **(scope or {})})
        result = self._client.action(spec, int(record["id"]), action_spec.path, payload=payload)
        return _to_job(result, action=action)


def _to_job(payload: dict[str, Any], *, action: str) -> Job:
    """Coerce a launch/update response into a :class:`Job` entity."""
    inferred_kind = _KIND_OF_ACTION_RESULT.get(action, "job")
    kind = payload.get("type") or inferred_kind
    return Job.model_validate({**payload, "kind": kind})
