"""Unit tests for the ``RunAction`` use case."""

from __future__ import annotations

from typing import Any, cast

import pytest

from untaped.capabilities.awx.application import RunAction
from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.domain import ActionPayload, ResourceSpec, ServerRecord
from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure.specs import JOB_TEMPLATE_SPEC


class _StubClient:
    """Minimal stub covering ``find_by_identity`` + ``action``.

    Records each ``action`` call as ``(record_id, action_name, body)``
    so tests can assert the launch went to the right record with the
    right payload shape.
    """

    def __init__(
        self,
        *,
        find_result: dict[str, Any],
        action_result: dict[str, Any],
    ) -> None:
        self._find_result = find_result
        self._action_result = action_result
        self.action_calls: list[tuple[int, str, dict[str, Any]]] = []

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        return ServerRecord(**self._find_result)

    def action(
        self,
        spec: ResourceSpec,
        id_: int,
        action: str,
        payload: ActionPayload | None = None,
    ) -> dict[str, Any]:
        body = payload.model_dump() if payload is not None else {}
        self.action_calls.append((id_, action, body))
        return self._action_result


def test_run_action_finds_then_posts() -> None:
    client = _StubClient(
        find_result={"id": 42, "name": "deploy"},
        action_result={"id": 7, "status": "pending", "type": "job"},
    )
    use = RunAction(cast(ResourceClient, client))
    job = use(
        JOB_TEMPLATE_SPEC,
        name="deploy",
        action="launch",
        scope={"organization": "Default"},
        payload={"limit": "web*"},
    )
    assert job.id == 7
    assert job.status == "pending"
    assert client.action_calls[0] == (42, "launch", {"limit": "web*"})


def test_run_action_unknown_action_errors() -> None:
    # action_result is unreachable in this path — RunAction rejects the
    # unknown action before reaching the client's action() call.
    client = _StubClient(find_result={"id": 1, "name": "x"}, action_result={})
    use = RunAction(cast(ResourceClient, client))
    with pytest.raises(AwxApiError):
        use(JOB_TEMPLATE_SPEC, name="x", action="not-real")


@pytest.mark.parametrize(
    "kind,action,result_kind",
    [
        ("JobTemplate", "launch", "job"),
        ("WorkflowJobTemplate", "launch", "workflow_job"),
        ("Project", "sync", "project_update"),
        ("InventorySource", "sync", "inventory_update"),
    ],
)
def test_fixed_action_uses_declared_result_kind_without_response_type(
    kind: str,
    action: str,
    result_kind: str,
) -> None:
    from untaped.capabilities.awx.infrastructure.specs import ALL_SPECS

    spec = next(s for s in ALL_SPECS if s.kind == kind)
    client = _StubClient(find_result={"id": 999}, action_result={"id": 101, "status": "pending"})
    job = RunAction(cast(ResourceClient, client)).execute(spec, 42, action=action)
    assert job.kind == result_kind
    assert job.id == 101
    assert client.action_calls == [(42, "launch" if action == "launch" else "update", {})]


def test_fixed_action_rejects_conflicting_response_kind_without_retry() -> None:
    client = _StubClient(
        find_result={"id": 999},
        action_result={"id": 101, "status": "pending", "type": "inventory_update"},
    )
    with pytest.raises(AwxApiError, match="unexpected execution kind"):
        RunAction(cast(ResourceClient, client)).execute(JOB_TEMPLATE_SPEC, 42, action="launch")
    assert len(client.action_calls) == 1
