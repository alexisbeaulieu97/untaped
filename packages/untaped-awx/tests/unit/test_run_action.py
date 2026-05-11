"""Unit tests for the ``RunAction`` use case."""

from __future__ import annotations

from typing import Any, cast

import pytest
from untaped_awx.application import RunAction
from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ActionPayload, ResourceSpec, ServerRecord
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC


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
