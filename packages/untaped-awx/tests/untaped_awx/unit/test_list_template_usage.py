"""Unit tests for the ``ListTemplateUsage`` use case."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from untaped_awx.application import ListTemplateUsage
from untaped_awx.application.resources.ports import ResourceClient, WorkflowNodeRepository
from untaped_awx.domain import ResourceSpec, ServerRecord, WorkflowUsage
from untaped_awx.domain.workflow_usage import UsageTarget
from untaped_awx.errors import ResourceNotFoundError
from untaped_awx.infrastructure.resources.specs.job_template import JOB_TEMPLATE_SPEC
from untaped_awx.infrastructure.resources.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC


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

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        self.calls.append(("find", params))
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


_TARGET = ServerRecord(id=10, name="smoke-test")


def _use(nodes: _StubNodes, resources: _StubResources | None = None) -> ListTemplateUsage:
    return ListTemplateUsage(
        cast(WorkflowNodeRepository, nodes),
        cast(ResourceClient, resources or _StubResources(_TARGET)),
    )


def _links(row: WorkflowUsage) -> list[tuple[int, str, int]]:
    return [(link.id, link.type, link.node_count) for link in row.contains]


def test_a_diamond_is_one_row_at_its_shallowest_depth_holding_both_links() -> None:
    # Grandparent (900) contains the target directly AND contains the
    # parent (100), which also contains the target.
    nodes = _StubNodes(
        {
            10: [
                _ref(1, wf_id=100, wf_name="parent"),
                _ref(2, wf_id=900, wf_name="grandparent"),
                _ref(4, wf_id=900, wf_name="grandparent"),
            ],
            100: [_ref(3, wf_id=900, wf_name="grandparent")],
            900: [],
        }
    )
    answer = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True, max_depth=None)

    assert answer.target == UsageTarget(id=10, name="smoke-test", type="job_template")
    assert {u.target for u in answer.rows} == {answer.target}
    assert [(u.id, u.depth, u.node_count, u.stopped, u.top_level) for u in answer.rows] == [
        (100, 0, 1, None, False),
        (900, 0, 2, None, True),
    ]
    assert _links(answer.rows[1]) == [
        (10, "job_template", 2),
        (100, "workflow_job_template", 1),
    ]
    # Each workflow's containers are looked up once.
    assert nodes.calls == [10, 100, 900]


def test_the_depth_limit_marks_the_rows_whose_containers_were_not_looked_up() -> None:
    nodes = _StubNodes({10: [_ref(1, wf_id=100)], 100: [_ref(2, wf_id=200)]})

    rows = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True, max_depth=1).rows

    assert [(u.id, u.depth, u.stopped) for u in rows] == [(100, 0, None), (200, 1, "depth")]
    assert nodes.calls == [10, 100]


def test_missing_summary_fields_degrades_to_none_name() -> None:
    raw = {"id": 1, "workflow_job_template": 100}
    nodes = _StubNodes({10: [raw]})
    rows = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True).rows
    assert [(u.id, u.name, u.node_count) for u in rows] == [(100, None, 1)]


def test_a_cycle_is_a_link_like_any_other() -> None:
    nodes = _StubNodes(
        {
            10: [_ref(1, wf_id=100, wf_name="a")],
            100: [_ref(2, wf_id=200, wf_name="b")],
            200: [_ref(3, wf_id=100, wf_name="a")],
        }
    )

    rows = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True, max_depth=None).rows

    assert [(u.id, _links(u)) for u in rows] == [
        (100, [(10, "job_template", 1), (200, "workflow_job_template", 1)]),
        (200, [(100, "workflow_job_template", 1)]),
    ]


def test_a_workflow_in_its_own_ancestry_is_a_row_of_its_own_usage() -> None:
    nodes = _StubNodes({200: [_ref(1, wf_id=100, wf_name="alpha")], 100: [_ref(2, wf_id=200)]})
    resources = _StubResources(ServerRecord(id=200, name="beta"))

    answer = _use(nodes, resources)(WORKFLOW_JOB_TEMPLATE_SPEC, identifier="beta", max_depth=None)

    assert answer.target.type == "workflow_job_template"
    assert [(u.id, u.depth, u.stopped, _links(u)) for u in answer.rows] == [
        (100, 0, None, [(200, "workflow_job_template", 1)]),
        (200, 1, None, [(100, "workflow_job_template", 1)]),
    ]
    # Its containers were the first lookup: never looked up again.
    assert nodes.calls == [200, 100]


def test_by_id_reads_the_target_record_and_a_missing_one_is_not_found() -> None:
    resources = _StubResources(_TARGET)
    _use(_StubNodes({}), resources)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True)
    assert resources.calls == [("find", {"id": "10"})]

    with pytest.raises(ResourceNotFoundError, match=r"JobTemplate not found \(id=999\)"):
        _use(_StubNodes({}), _StubResources())(JOB_TEMPLATE_SPEC, identifier="999", by_id=True)


def test_a_name_resolves_within_its_scope() -> None:
    resources = _StubResources(_TARGET)

    answer = _use(_StubNodes({}), resources)(
        JOB_TEMPLATE_SPEC, identifier="smoke-test", scope={"organization__name": "Default"}
    )

    assert answer.target.name == "smoke-test"
    assert resources.calls == [("smoke-test", {"organization__name": "Default"})]
