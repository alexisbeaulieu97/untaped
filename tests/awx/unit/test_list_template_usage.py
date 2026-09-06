"""Unit tests for the ``ListTemplateUsage`` use case."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, cast

import pytest

from untaped.capabilities.awx.application import ListTemplateUsage
from untaped.capabilities.awx.application.ports import ResourceClient, WorkflowNodeRepository
from untaped.capabilities.awx.domain import ResourceSpec, ServerRecord
from untaped.capabilities.awx.errors import ResourceNotFound
from untaped.capabilities.awx.infrastructure.specs.job_template import JOB_TEMPLATE_SPEC
from untaped.capabilities.awx.infrastructure.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC


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


def test_lists_direct_parents_with_default_depth_zero() -> None:
    nodes = _StubNodes(
        {
            10: [
                _ref(1, wf_id=100, wf_name="alpha"),
                _ref(2, wf_id=200, wf_name="beta"),
            ],
        }
    )
    result = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True)
    assert [(u.id, u.name, u.depth, u.node_count) for u in result] == [
        (100, "alpha", 0, 1),
        (200, "beta", 0, 1),
    ]
    assert nodes.calls == [10]


def test_multiple_references_in_one_workflow_collapse_to_one_row() -> None:
    nodes = _StubNodes(
        {
            10: [
                _ref(1, wf_id=100, wf_name="alpha"),
                _ref(2, wf_id=100, wf_name="alpha"),
            ],
        }
    )
    result = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True)
    assert [(u.id, u.node_count) for u in result] == [(100, 2)]


def test_by_id_identifier_skips_name_lookup() -> None:
    resources = _StubResources()
    _use(_StubNodes({}), resources)(JOB_TEMPLATE_SPEC, identifier="42", by_id=True)
    assert resources.calls == []


def test_numeric_identifier_defaults_to_name_lookup() -> None:
    resources = _StubResources(found=ServerRecord(id=42, name="42"))
    nodes = _StubNodes({42: []})
    _use(nodes, resources)(JOB_TEMPLATE_SPEC, identifier="42")
    assert resources.calls == [("42", None)]
    assert nodes.calls == [42]


def test_name_identifier_resolves_via_find_by_identity_with_scope() -> None:
    resources = _StubResources(found=ServerRecord(id=77, name="deploy-app"))
    nodes = _StubNodes({77: []})
    _use(nodes, resources)(
        JOB_TEMPLATE_SPEC,
        identifier="deploy-app",
        scope={"organization": "Default"},
    )
    assert resources.calls == [("deploy-app", {"organization": "Default"})]
    assert nodes.calls == [77]


def test_unknown_name_raises_resource_not_found() -> None:
    use = _use(_StubNodes({}), _StubResources(found=None))
    with pytest.raises(ResourceNotFound):
        use(JOB_TEMPLATE_SPEC, identifier="does-not-exist")


def test_zero_usages_returns_empty_list() -> None:
    result = _use(_StubNodes({}))(JOB_TEMPLATE_SPEC, identifier="10", by_id=True)
    assert result == []


def test_default_depth_zero_does_not_query_parents_of_parents() -> None:
    nodes = _StubNodes(
        {
            10: [_ref(1, wf_id=100, wf_name="parent")],
            100: [_ref(2, wf_id=900, wf_name="grandparent")],
        }
    )
    result = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True)
    assert [(u.id, u.depth) for u in result] == [(100, 0)]
    assert nodes.calls == [10]


def test_unlimited_depth_walks_up_ancestry_breadth_first() -> None:
    nodes = _StubNodes(
        {
            10: [_ref(1, wf_id=100, wf_name="parent")],
            100: [_ref(2, wf_id=900, wf_name="grandparent")],
            900: [],
        }
    )
    result = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True, max_depth=None)
    assert [(u.id, u.name, u.depth) for u in result] == [
        (100, "parent", 0),
        (900, "grandparent", 1),
    ]
    assert nodes.calls == [10, 100, 900]


def test_max_depth_caps_ancestry_walk() -> None:
    nodes = _StubNodes(
        {
            10: [_ref(1, wf_id=100, wf_name="parent")],
            100: [_ref(2, wf_id=900, wf_name="grandparent")],
            900: [_ref(3, wf_id=950, wf_name="great-grandparent")],
        }
    )
    result = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True, max_depth=1)
    assert [(u.id, u.depth) for u in result] == [(100, 0), (900, 1)]
    assert nodes.calls == [10, 100]


def test_cycle_guard_emits_warning_and_terminates() -> None:
    # A (100) contains B (200); B contains A. Looking up B's usage finds A,
    # then walking A's parents finds B again — a true cycle.
    nodes = _StubNodes(
        {
            200: [_ref(1, wf_id=100, wf_name="A")],
            100: [_ref(2, wf_id=200, wf_name="B")],
        }
    )
    warnings: list[str] = []
    result = _use(nodes, warn=warnings.append)(
        WORKFLOW_JOB_TEMPLATE_SPEC, identifier="200", by_id=True, max_depth=None
    )
    assert [(u.id, u.depth) for u in result] == [(100, 0)]
    assert len(warnings) == 1
    assert "cycle" in warnings[0]
    assert "200" in warnings[0]


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


def test_filters_passed_to_every_ancestry_level() -> None:
    nodes = _StubNodes(
        {
            10: [_ref(1, wf_id=100, wf_name="parent")],
            100: [_ref(2, wf_id=900, wf_name="grandparent")],
            900: [],
        }
    )
    filters = {"workflow_job_template__organization__name": "Default"}
    _use(nodes)(
        JOB_TEMPLATE_SPEC,
        identifier="10",
        by_id=True,
        max_depth=None,
        filters=filters,
    )
    assert nodes.calls == [10, 100, 900]
    assert nodes.params_received == [filters, filters, filters]


def test_filters_default_to_none_when_unset() -> None:
    nodes = _StubNodes({10: [_ref(1, wf_id=100, wf_name="alpha")]})
    _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True)
    assert nodes.params_received == [None]


def test_missing_summary_fields_degrades_to_none_name() -> None:
    raw = {"id": 1, "workflow_job_template": 100}
    nodes = _StubNodes({10: [raw]})
    result = _use(nodes)(JOB_TEMPLATE_SPEC, identifier="10", by_id=True)
    assert [(u.id, u.name, u.node_count) for u in result] == [(100, None, 1)]
