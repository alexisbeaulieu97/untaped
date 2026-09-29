"""The ``WorkflowJobTemplate`` ``spec.nodes`` format: shape and graph validation."""

from __future__ import annotations

from typing import Any

import pytest

from untaped.capabilities.awx.domain.workflow_graph import (
    WorkflowNodeSpec,
    dump_workflow_nodes,
    parse_workflow_nodes,
    rename_references,
)


def _node(id_: str, **fields: Any) -> dict[str, Any]:
    return {"id": id_, "run": {"job_template": id_.title()}, **fields}


def test_a_full_graph_parses_and_dumps_without_defaults() -> None:
    nodes = parse_workflow_nodes(
        [
            {
                "id": "deploy",
                "run": {"job_template": "Deploy"},
                "prompts": {"limit": "web*", "diff_mode": False, "credentials": ["ssh"]},
                "success": ["verify"],
                "failure": ["rollback"],
            },
            {"id": "approve", "approval": {"name": "Approve"}, "success": ["deploy"]},
            {
                "id": "verify",
                "run": {"inventory_source": "AWS", "inventory": "Cloud", "organization": "Ops"},
                "all_parents_must_converge": True,
            },
            _node("rollback"),
        ]
    )

    assert [node.id for node in nodes] == ["deploy", "approve", "verify", "rollback"]
    assert nodes[2].run is not None
    assert (nodes[2].run.kind, nodes[2].run.name) == ("InventorySource", "AWS")
    assert dump_workflow_nodes(nodes) == [
        {
            "id": "deploy",
            "run": {"job_template": "Deploy"},
            "prompts": {"credentials": ["ssh"], "limit": "web*", "diff_mode": False},
            "success": ["verify"],
            "failure": ["rollback"],
        },
        {"id": "approve", "approval": {"name": "Approve"}, "success": ["deploy"]},
        {
            "id": "verify",
            "run": {"inventory_source": "AWS", "organization": "Ops", "inventory": "Cloud"},
            "all_parents_must_converge": True,
        },
        {"id": "rollback", "run": {"job_template": "Rollback"}},
    ]


@pytest.mark.parametrize(
    ("nodes", "message"),
    [
        ("deploy", "must be a list"),
        ([_node("a"), _node("a")], "duplicate node id 'a'"),
        ([_node("a", success=["b"])], "node 'a' success: unknown node id 'b'"),
        ([_node("a", success=["a"])], "cycle: a → a"),
        (
            [_node("a", success=["b"]), _node("b", always=["c"]), _node("c", failure=["a"])],
            "cycle: a → b → c → a",
        ),
        (
            [_node("a", success=["b"], failure=["b"]), _node("b")],
            "node 'a' lists 'b' under both success and failure",
        ),
        ([_node("a", success=["b", "b"]), _node("b")], "node 'a' success lists 'b' twice"),
        ([{"id": "a"}], "exactly one of run or approval"),
        (
            [{"id": "a", "run": {"job_template": "A"}, "approval": {"name": "ok"}}],
            "exactly one of run or approval",
        ),
        ([{"id": "a", "run": {"organization": "Default"}}], "exactly one of job_template"),
        (
            [{"id": "a", "run": {"job_template": "A", "project": "P"}}],
            "exactly one of job_template",
        ),
        (
            [{"id": "a", "run": {"job_template": "A", "inventory": "I"}}],
            "inventory only applies to inventory_source",
        ),
        (
            [{"id": "a", "run": {"inventory_source": "AWS"}}],
            "run.inventory_source needs run.inventory",
        ),
        (
            [{"id": "a", "run": {"system_job_template": "Cleanup", "organization": "X"}}],
            "run.organization does not apply to system_job_template",
        ),
        (
            [{"id": "a", "approval": {"name": "ok"}, "prompts": {"limit": "x"}}],
            "approval nodes take no prompts",
        ),
        ([_node("a", prompts={"limt": "x"})], "nodes[0].prompts.limt"),
    ],
)
def test_invalid_graphs_are_refused(nodes: Any, message: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_workflow_nodes(nodes)
    assert message in str(caught.value)


def test_node_references_name_their_kinds() -> None:
    node = WorkflowNodeSpec.model_validate({"id": "a", "run": {"workflow_job_template": "Sub"}})
    assert node.run is not None
    assert (node.run.kind, node.run.name, node.run.organization) == (
        "WorkflowJobTemplate",
        "Sub",
        None,
    )


def test_an_explicit_null_organization_survives_a_dump() -> None:
    """``organization: null`` names a global template; leaving it out means the workflow's."""
    nodes = parse_workflow_nodes(
        [
            {"id": "a", "run": {"job_template": "Global", "organization": None}},
            {"id": "b", "run": {"system_job_template": "Cleanup Job Details"}},
        ]
    )

    assert dump_workflow_nodes(nodes) == [
        {"id": "a", "run": {"job_template": "Global", "organization": None}},
        {"id": "b", "run": {"system_job_template": "Cleanup Job Details"}},
    ]
    assert nodes[1].run is not None
    assert nodes[1].run.kind == "SystemJobTemplate"


def test_rename_references_rewrites_runs_and_prompts_by_kind_and_name() -> None:
    nodes = parse_workflow_nodes(
        [
            {
                "id": "a",
                "run": {"job_template": "Deploy", "organization": "Ops"},
                "prompts": {
                    "inventory": "Lab",
                    "credentials": ["ssh", {"name": "vault", "organization": "Ops"}],
                    "limit": "web",
                },
                "success": ["b"],
            },
            {"id": "b", "run": {"workflow_job_template": "Deploy"}},
        ]
    )

    renamed = rename_references(
        nodes,
        {
            ("JobTemplate", "Deploy"): "Deploy [test]",
            ("Inventory", "Lab"): "Lab [test]",
            ("Credential", "vault"): "vault [test]",
        },
    )

    assert dump_workflow_nodes(renamed) == [
        {
            "id": "a",
            "run": {"job_template": "Deploy [test]", "organization": "Ops"},
            "prompts": {
                "inventory": "Lab [test]",
                "credentials": ["ssh", {"name": "vault [test]", "organization": "Ops"}],
                "limit": "web",
            },
            "success": ["b"],
        },
        {"id": "b", "run": {"workflow_job_template": "Deploy"}},
    ]
    assert dump_workflow_nodes(nodes)[0]["run"]["job_template"] == "Deploy"
