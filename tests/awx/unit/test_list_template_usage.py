"""Unit tests for the ``ListTemplateUsage`` use case."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, cast

from untaped.capabilities.awx.application import ListTemplateUsage
from untaped.capabilities.awx.application.ports import ResourceClient, WorkflowNodeRepository
from untaped.capabilities.awx.domain import ResourceSpec, ServerRecord
from untaped.capabilities.awx.infrastructure.specs.job_template import JOB_TEMPLATE_SPEC


class _StubNodes:
    def __init__(self, by_child: dict[int, list[dict[str, Any]]]) -> None:
        self._by_child = by_child
        self.calls: list[int] = []
        self.params_received: list[dict[str, str] | None] = []

    def list_references(
        self,
        *,
        unified_job_template: int,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(unified_job_template)
        self.params_received.append(params)
        return iter(self._by_child.get(unified_job_template, []))


class _StubResources:
    def __init__(self, found: ServerRecord | None = None) -> None:
        self.found = found
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        self.calls.append((name, scope))
        return self.found


def _ref(
    node_id: int,
    *,
    wf_id: int,
    wf_name: str | None = None,
) -> dict[str, Any]:
    """Build a workflow_job_template_nodes record shaped like AWX's API response."""
    summary: dict[str, Any] = {}
    if wf_name is not None:
        summary["workflow_job_template"] = {"id": wf_id, "name": wf_name}
    return {
        "id": node_id,
        "workflow_job_template": wf_id,
        "summary_fields": summary,
    }


def _use(
    nodes: _StubNodes,
    resources: _StubResources | None = None,
    *,
    warn: Callable[[str], None] = lambda _msg: None,
) -> ListTemplateUsage:
    return ListTemplateUsage(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, resources or _StubResources()),
        warn=warn,
    )


def test_diamond_emits_one_row_at_shallowest_depth_without_warning() -> None:
    # Grandparent (900) contains the target directly AND contains the
    # parent (100), which also contains the target. 900 must appear once,
    # at depth 0, with its direct-reference count — and no cycle warning.
    nodes = _StubNodes(
        {
            10: [
                _ref(1, wf_id=100, wf_name="parent"),
                _ref(2, wf_id=900, wf_name="grandparent"),
            ],
            100: [_ref(3, wf_id=900, wf_name="grandparent")],
            900: [],
        }
    )
    warnings: list[str] = []
    result = _use(nodes, warn=warnings.append)(
        JOB_TEMPLATE_SPEC, identifier="10", by_id=True, max_depth=None
    )
    assert [(u.id, u.depth, u.node_count) for u in result] == [
        (100, 0, 1),
        (900, 0, 1),
    ]
    assert warnings == []


def test_missing_summary_fields_degrades_to_none_name() -> None:
    raw = {"id": 1, "workflow_job_template": 100}
    nodes = _StubNodes({10: [raw]})
    result = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True)
    assert [(u.id, u.name, u.node_count) for u in result] == [(100, None, 1)]
