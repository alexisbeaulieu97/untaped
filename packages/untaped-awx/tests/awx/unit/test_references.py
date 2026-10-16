"""``references_of``: each reference a document makes, with the scope apply resolves it in."""

from __future__ import annotations

from typing import Any

from untaped_awx.application.references import ReferenceSite, references_of
from untaped_awx.domain import Resource
from untaped_awx.domain.workflow_graph import NameRef, NodeRun
from untaped_awx.infrastructure.specs import (
    JOB_TEMPLATE_SPEC,
    SCHEDULE_SPEC,
    WORKFLOW_JOB_TEMPLATE_SPEC,
)

_DEFAULT = {"organization": "Default"}


def _doc(kind: str, metadata: dict[str, Any], **spec: Any) -> Resource:
    return Resource.model_validate({"kind": kind, "metadata": metadata, "spec": spec})


def test_foreign_keys_expand_multi_values_and_keep_their_scope() -> None:
    doc = _doc(
        "JobTemplate",
        {"name": "deploy", "organization": "Default"},
        project="playbooks",
        inventory=7,
        webhook_credential=None,
        credentials=["ssh", {"name": "vault", "organization": "Ops"}],
        instance_groups=["default"],
    )

    assert references_of(JOB_TEMPLATE_SPEC, doc) == (
        ReferenceSite("project", "Project", "playbooks", _DEFAULT),
        ReferenceSite("inventory", "Inventory", 7, _DEFAULT),
        ReferenceSite("credentials", "Credential", "ssh", _DEFAULT),
        ReferenceSite(
            "credentials", "Credential", {"name": "vault", "organization": "Ops"}, _DEFAULT
        ),
        ReferenceSite("instance_groups", "InstanceGroup", "default", None),
    )


def test_a_schedule_references_its_parent_and_scopes_by_the_parent_org() -> None:
    parent = {"kind": "JobTemplate", "name": "deploy", "organization": "Ops"}
    doc = _doc("Schedule", {"name": "nightly", "parent": parent}, rrule="x", inventory="staging")

    assert references_of(SCHEDULE_SPEC, doc) == (
        ReferenceSite("parent", "JobTemplate", {**parent, "parent": None}, None),
        ReferenceSite("inventory", "Inventory", "staging", {"organization": "Ops"}),
    )


def test_workflow_nodes_reference_what_they_run_and_prompt_with() -> None:
    nodes = [
        {"id": "approve", "approval": {"name": "Approve"}, "success": ["deploy"]},
        {
            "id": "deploy",
            "run": {"job_template": "deploy"},
            "prompts": {
                "inventory": {"name": "edge", "organization": "Ops"},
                "credentials": ["ssh", "vault"],
            },
        },
    ]
    doc = _doc("WorkflowJobTemplate", {"name": "release", "organization": "Default"}, nodes=nodes)

    assert references_of(WORKFLOW_JOB_TEMPLATE_SPEC, doc) == (
        ReferenceSite("nodes[deploy].run", "JobTemplate", NodeRun(job_template="deploy"), _DEFAULT),
        ReferenceSite(
            "nodes[deploy].prompts.inventory",
            "Inventory",
            NameRef(name="edge", organization="Ops"),
            _DEFAULT,
        ),
        ReferenceSite("nodes[deploy].prompts.credentials", "Credential", "ssh", _DEFAULT),
        ReferenceSite("nodes[deploy].prompts.credentials", "Credential", "vault", _DEFAULT),
    )


def test_an_invalid_node_graph_references_nothing() -> None:
    doc = _doc(
        "WorkflowJobTemplate",
        {"name": "release", "organization": None},
        inventory="prod",
        nodes=[{"id": "a", "run": {"job_template": "x"}, "success": ["missing"]}],
    )

    assert references_of(WORKFLOW_JOB_TEMPLATE_SPEC, doc) == (
        ReferenceSite("inventory", "Inventory", "prod", None),
    )
