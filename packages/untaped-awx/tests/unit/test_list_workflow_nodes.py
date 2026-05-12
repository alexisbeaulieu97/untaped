"""Unit tests for the ``ListWorkflowNodes`` use case."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from untaped_awx.application import ListWorkflowNodes
from untaped_awx.application.ports import ResourceClient, WorkflowNodeRepository
from untaped_awx.domain import ResourceSpec, ServerRecord
from untaped_awx.errors import ResourceNotFound
from untaped_awx.infrastructure.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC


class _StubNodes:
    def __init__(self, by_workflow: dict[int, list[dict[str, Any]]]) -> None:
        self._by_workflow = by_workflow
        self.calls: list[int] = []

    def list_nodes(self, *, workflow_id: int) -> Iterator[dict[str, Any]]:
        self.calls.append(workflow_id)
        return iter(self._by_workflow.get(workflow_id, []))


class _StubResources:
    """Minimal stand-in for ``ResourceClient.find_by_identity``.

    Only the one method the use case actually calls; tests that pass
    a numeric identifier never hit the lookup path so the no-op default
    is enough."""

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


def _node(
    node_id: int,
    *,
    identifier: str | None = None,
    ujt_id: int | None,
    ujt_name: str | None = None,
    ujt_type: str | None = None,
) -> dict[str, Any]:
    """Build a workflow-nodes record shaped like AWX's API response."""
    summary: dict[str, Any] = {}
    if ujt_id is not None:
        summary["unified_job_template"] = {
            "id": ujt_id,
            "name": ujt_name,
            "unified_job_type": ujt_type,
        }
    return {
        "id": node_id,
        "identifier": identifier,
        "unified_job_template": ujt_id,
        "summary_fields": summary,
    }


def test_lists_top_level_nodes_with_depth_zero() -> None:
    nodes = _StubNodes(
        {
            100: [
                _node(1, identifier="a", ujt_id=10, ujt_name="alpha", ujt_type="job_template"),
                _node(2, identifier="b", ujt_id=11, ujt_name="beta", ujt_type="job_template"),
            ],
        }
    )
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100")
    assert [n.id for n in result] == [1, 2]
    assert all(n.depth == 0 for n in result)
    assert [n.name for n in result] == ["alpha", "beta"]
    assert nodes.calls == [100]


def test_numeric_identifier_skips_name_lookup() -> None:
    resources = _StubResources()
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, _StubNodes({42: []})),
        cast(ResourceClient, resources),
    )
    use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="42")
    assert resources.calls == []


def test_name_identifier_resolves_via_find_by_identity() -> None:
    resources = _StubResources(found=ServerRecord(id=77, name="weekly-rollup"))
    nodes = _StubNodes({77: []})
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, resources),
    )
    use(
        WORKFLOW_JOB_TEMPLATE_SPEC,
        identifier="weekly-rollup",
        scope={"organization": "Default"},
    )
    assert resources.calls == [("weekly-rollup", {"organization": "Default"})]
    assert nodes.calls == [77]


def test_unknown_name_raises_resource_not_found() -> None:
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, _StubNodes({})),
        cast(ResourceClient, _StubResources(found=None)),
    )
    with pytest.raises(ResourceNotFound):
        use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="does-not-exist")


def test_recursive_expands_sub_workflows_unlimited() -> None:
    nodes = _StubNodes(
        {
            100: [
                _node(1, identifier="run", ujt_id=10, ujt_name="alpha", ujt_type="job_template"),
                _node(
                    2,
                    identifier="rollup",
                    ujt_id=200,
                    ujt_name="nested",
                    ujt_type="workflow_job_template",
                ),
            ],
            200: [
                _node(3, identifier="x", ujt_id=11, ujt_name="beta", ujt_type="job_template"),
                _node(4, identifier="y", ujt_id=12, ujt_name="gamma", ujt_type="job_template"),
            ],
        }
    )
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", recursive=True)
    assert [(n.id, n.depth) for n in result] == [(1, 0), (2, 0), (3, 1), (4, 1)]


def test_max_depth_caps_recursion() -> None:
    # 100 → 200 → 300; max_depth=1 should stop after pulling 200's nodes
    # (depth 1) and not enter 300.
    nodes = _StubNodes(
        {
            100: [
                _node(
                    1,
                    identifier="r1",
                    ujt_id=200,
                    ujt_name="lvl1",
                    ujt_type="workflow_job_template",
                ),
            ],
            200: [
                _node(
                    2,
                    identifier="r2",
                    ujt_id=300,
                    ujt_name="lvl2",
                    ujt_type="workflow_job_template",
                ),
            ],
            300: [
                _node(3, identifier="leaf", ujt_id=99, ujt_name="leaf", ujt_type="job_template"),
            ],
        }
    )
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(
        WORKFLOW_JOB_TEMPLATE_SPEC,
        identifier="100",
        recursive=True,
        max_depth=1,
    )
    assert [(n.id, n.depth) for n in result] == [(1, 0), (2, 1)]
    assert 300 not in nodes.calls


def test_max_depth_zero_returns_only_root() -> None:
    nodes = _StubNodes(
        {
            100: [
                _node(
                    1,
                    identifier="r1",
                    ujt_id=200,
                    ujt_name="lvl1",
                    ujt_type="workflow_job_template",
                ),
            ],
            200: [
                _node(2, identifier="leaf", ujt_id=99, ujt_name="leaf", ujt_type="job_template"),
            ],
        }
    )
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(
        WORKFLOW_JOB_TEMPLATE_SPEC,
        identifier="100",
        recursive=True,
        max_depth=0,
    )
    assert [(n.id, n.depth) for n in result] == [(1, 0)]
    assert nodes.calls == [100]


def test_cycle_guard_emits_warning_and_skips() -> None:
    # A → B → A. Without the visited set this would loop forever.
    nodes = _StubNodes(
        {
            100: [
                _node(
                    1,
                    identifier="to-b",
                    ujt_id=200,
                    ujt_name="B",
                    ujt_type="workflow_job_template",
                ),
            ],
            200: [
                _node(
                    2,
                    identifier="back-to-a",
                    ujt_id=100,
                    ujt_name="A",
                    ujt_type="workflow_job_template",
                ),
            ],
        }
    )
    warnings: list[str] = []
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
        warn=warnings.append,
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", recursive=True)
    # We see node 1 (depth 0) and node 2 (depth 1, the back-edge node
    # itself). Recursion into workflow 100 from depth 2 is blocked by
    # the visited guard.
    assert [(n.id, n.depth) for n in result] == [(1, 0), (2, 1)]
    assert len(warnings) == 1
    assert "cycle" in warnings[0]
    assert "100" in warnings[0]


def test_missing_summary_fields_degrades_to_none() -> None:
    raw = {"id": 5, "identifier": None, "unified_job_template": 99}
    nodes = _StubNodes({100: [raw]})
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100")
    assert result[0].unified_job_template == 99
    assert result[0].name is None
    assert result[0].type is None
    assert result[0].identifier is None


def test_deleted_template_carries_null_unified_job_template() -> None:
    raw = {"id": 5, "identifier": "orphan", "unified_job_template": None, "summary_fields": {}}
    nodes = _StubNodes({100: [raw]})
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", recursive=True)
    # No recursion attempt — the FK is null, nothing to expand.
    assert nodes.calls == [100]
    assert result[0].unified_job_template is None
