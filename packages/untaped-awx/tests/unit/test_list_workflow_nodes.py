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
        self.params_received: list[dict[str, str] | None] = []

    def list_nodes(
        self,
        *,
        workflow_id: int,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(workflow_id)
        self.params_received.append(params)
        return iter(self._by_workflow.get(workflow_id, []))


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


def test_lists_top_level_nodes_with_default_depth_zero() -> None:
    nodes = _StubNodes(
        {
            100: [
                _node(1, identifier="a", ujt_id=10, ujt_name="alpha", ujt_type="job"),
                _node(2, identifier="b", ujt_id=11, ujt_name="beta", ujt_type="job"),
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


def test_unlimited_depth_expands_sub_workflows() -> None:
    nodes = _StubNodes(
        {
            100: [
                _node(1, identifier="run", ujt_id=10, ujt_name="alpha", ujt_type="job"),
                _node(
                    2,
                    identifier="rollup",
                    ujt_id=200,
                    ujt_name="nested",
                    ujt_type="workflow_job",
                ),
            ],
            200: [
                _node(3, identifier="x", ujt_id=11, ujt_name="beta", ujt_type="job"),
                _node(4, identifier="y", ujt_id=12, ujt_name="gamma", ujt_type="job"),
            ],
        }
    )
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", max_depth=None)
    assert [(n.id, n.depth) for n in result] == [(1, 0), (2, 0), (3, 1), (4, 1)]


def test_max_depth_caps_recursion() -> None:
    nodes = _StubNodes(
        {
            100: [
                _node(
                    1,
                    identifier="r1",
                    ujt_id=200,
                    ujt_name="lvl1",
                    ujt_type="workflow_job",
                ),
            ],
            200: [
                _node(
                    2,
                    identifier="r2",
                    ujt_id=300,
                    ujt_name="lvl2",
                    ujt_type="workflow_job",
                ),
            ],
            300: [
                _node(3, identifier="leaf", ujt_id=99, ujt_name="leaf", ujt_type="job"),
            ],
        }
    )
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", max_depth=1)
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
                    ujt_type="workflow_job",
                ),
            ],
            200: [
                _node(2, identifier="leaf", ujt_id=99, ujt_name="leaf", ujt_type="job"),
            ],
        }
    )
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", max_depth=0)
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
                    ujt_type="workflow_job",
                ),
            ],
            200: [
                _node(
                    2,
                    identifier="back-to-a",
                    ujt_id=100,
                    ujt_name="A",
                    ujt_type="workflow_job",
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
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", max_depth=None)
    assert [(n.id, n.depth) for n in result] == [(1, 0), (2, 1)]
    assert len(warnings) == 1
    assert "cycle" in warnings[0]
    assert "100" in warnings[0]


def test_shared_sub_workflow_is_not_a_false_cycle() -> None:
    # Diamond: workflow 100 contains two nodes, each pointing at the same
    # child workflow 300. That's a shared sub-workflow, not a cycle. The
    # second reference must be skipped silently (no false-positive warning)
    # AND no second fetch of 300's nodes.
    nodes = _StubNodes(
        {
            100: [
                _node(1, identifier="a", ujt_id=300, ujt_name="shared", ujt_type="workflow_job"),
                _node(2, identifier="b", ujt_id=300, ujt_name="shared", ujt_type="workflow_job"),
            ],
            300: [
                _node(3, identifier="leaf", ujt_id=11, ujt_name="leaf", ujt_type="job"),
            ],
        }
    )
    warnings: list[str] = []
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
        warn=warnings.append,
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", max_depth=None)
    assert [(n.id, n.depth) for n in result] == [(1, 0), (2, 0), (3, 1)]
    assert warnings == []
    assert nodes.calls.count(300) == 1


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


def test_normalises_unified_job_type_to_template_type() -> None:
    # AWX returns ``unified_job_type`` (the *job* discriminator), not the
    # template type. Regression: an earlier revision checked
    # ``type == "workflow_job_template"`` against the raw ``"workflow_job"``
    # string and never recursed.
    nodes = _StubNodes(
        {
            100: [
                _node(1, identifier="j", ujt_id=10, ujt_name="jt", ujt_type="job"),
                _node(2, identifier="w", ujt_id=20, ujt_name="wf", ujt_type="workflow_job"),
                _node(3, identifier="p", ujt_id=30, ujt_name="proj", ujt_type="project_update"),
                _node(4, identifier="i", ujt_id=40, ujt_name="inv", ujt_type="inventory_update"),
            ],
            20: [
                _node(5, identifier="leaf", ujt_id=11, ujt_name="leaf", ujt_type="job"),
            ],
        }
    )
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", max_depth=None)
    assert [(n.id, n.type, n.depth) for n in result] == [
        (1, "job_template", 0),
        (2, "workflow_job_template", 0),
        (3, "project", 0),
        (4, "inventory_source", 0),
        (5, "job_template", 1),
    ]
    assert 20 in nodes.calls


def test_deleted_template_carries_null_unified_job_template() -> None:
    raw = {"id": 5, "identifier": "orphan", "unified_job_template": None, "summary_fields": {}}
    nodes = _StubNodes({100: [raw]})
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    result = use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100", max_depth=None)
    assert nodes.calls == [100]
    assert result[0].unified_job_template is None


def test_filters_passed_to_every_recursive_list_nodes_call() -> None:
    # ``filters`` must reach every BFS-level fetch unchanged — otherwise
    # the server-side scope only applies at the root and silently
    # widens for deeper levels.
    nodes = _StubNodes(
        {
            100: [
                _node(1, identifier="leaf", ujt_id=10, ujt_name="j", ujt_type="job"),
                _node(2, identifier="sub", ujt_id=200, ujt_name="b", ujt_type="workflow_job"),
            ],
            200: [
                _node(3, identifier="deep", ujt_id=11, ujt_name="k", ujt_type="job"),
            ],
        }
    )
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    filters = {"unified_job_template__in": "10,11,200"}
    use(
        WORKFLOW_JOB_TEMPLATE_SPEC,
        identifier="100",
        max_depth=None,
        filters=filters,
    )
    assert nodes.calls == [100, 200]
    assert nodes.params_received == [filters, filters]


def test_filters_default_to_none_when_unset() -> None:
    nodes = _StubNodes({100: [_node(1, identifier="j", ujt_id=10, ujt_name="j", ujt_type="job")]})
    use = ListWorkflowNodes(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, _StubResources()),
    )
    use(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="100")
    assert nodes.params_received == [None]
