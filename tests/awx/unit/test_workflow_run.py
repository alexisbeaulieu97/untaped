"""A workflow job's nodes as they ran: parsing, the node that failed it, summed host summaries."""

from __future__ import annotations

from typing import Any

from untaped.capabilities.awx.domain.workflow_run import (
    RunNode,
    TemplateNode,
    approval_labels,
    blamed_node,
    summed_host_records,
)


def _node(
    record_id: int, status: str, *, job_id: int | None = None, error_path: bool = False
) -> RunNode:
    return RunNode(
        record_id=record_id,
        identifier=f"n{record_id}",
        template="T",
        job_id=job_id if job_id is not None else record_id,
        kind="job",
        status=status,
        error_path=error_path,
    )


def test_a_workflow_is_blamed_on_the_first_failure_with_no_path_out() -> None:
    handled = _node(1, "failed", error_path=True)
    later = _node(2, "failed", job_id=20)
    first = _node(3, "error", job_id=10)

    assert blamed_node([handled, later, first, _node(4, "successful")]) == first


def test_failures_every_path_handled_blame_no_node() -> None:
    assert blamed_node([_node(1, "failed", error_path=True), _node(2, "successful")]) is None
    assert blamed_node([]) is None


def test_a_node_record_says_what_ran_and_how_it_ended() -> None:
    ran = RunNode.from_record(
        {
            "id": 7,
            "identifier": "deploy",
            "job": 70,
            "failure_nodes": [8],
            "summary_fields": {
                "job": {"id": 70, "type": "job", "status": "failed"},
                "unified_job_template": {"name": "Deploy", "unified_job_type": "job"},
            },
        }
    )
    never = RunNode.from_record({"id": 8, "identifier": None, "job": None, "summary_fields": {}})

    assert (ran.label, ran.template, ran.kind, ran.status, ran.error_path) == (
        "deploy",
        "Deploy",
        "job",
        "failed",
        True,
    )
    assert ran.execution is not None and (ran.execution.id, ran.execution.kind) == (70, "job")
    assert (never.label, never.status, never.execution) == ("#8", "never_ran", None)


def test_template_nodes_name_their_approvals() -> None:
    records: list[dict[str, Any]] = [
        {
            "id": 1,
            "identifier": "build",
            "unified_job_template": 5,
            "summary_fields": {
                "unified_job_template": {"name": "Build", "unified_job_type": "job"}
            },
        },
        {
            "id": 2,
            "identifier": "gate",
            "unified_job_template": 6,
            "summary_fields": {
                "unified_job_template": {"name": "Go?", "unified_job_type": "workflow_approval"}
            },
        },
    ]
    nodes = [TemplateNode.from_record(record) for record in records]

    assert [(node.label, node.template, node.template_id) for node in nodes] == [
        ("build", "Build", 5),
        ("gate", "Go?", 6),
    ]
    assert approval_labels(nodes) == ["gate"]


def test_host_summaries_sum_per_host_over_every_job_failed_hosts_first() -> None:
    summed = summed_host_records(
        [
            [{"host_name": "web1", "changed": 1, "ok": 2}, {"host_name": "db1", "ok": 1}],
            [{"host_name": "web1", "changed": 2, "failures": 1}],
        ]
    )

    assert [row["host_name"] for row in summed] == ["web1", "db1"]
    assert (summed[0]["changed"], summed[0]["ok"], summed[0]["failures"]) == (3, 2, 1)
    assert summed[0]["failed"] is True and summed[1]["failed"] is False
