"""``prefetch_plan``: the ``(kind, scope)`` groups a multi-document apply warms."""

from __future__ import annotations

import json
from typing import Any

from untaped_awx.application.resources.apply_prefetch import prefetch_plan
from untaped_awx.domain import Resource
from untaped_awx.infrastructure.resources.catalog import AwxResourceCatalog

_PROD = {"kind": "Inventory", "name": "prod", "organization": "Default"}

_BATCH: list[dict[str, Any]] = [
    {
        "kind": "Project",
        "metadata": {"name": "playbooks", "organization": "Default"},
        "spec": {
            "scm_type": "git",
            "credential": "git",
            "default_environment": "ee",
            "signature_validation_credential": None,
        },
    },
    {
        "kind": "JobTemplate",
        "metadata": {"name": "deploy", "organization": "Default"},
        "spec": {
            "project": "playbooks",
            "inventory": "prod",
            "execution_environment": "ee",
            "credentials": ["ssh", {"name": "vault", "organization": "Ops"}],
            "labels": ["prod"],
            "instance_groups": ["default", "fallback"],
            "webhook_credential": None,
        },
    },
    {
        "kind": "JobTemplate",
        "metadata": {"name": "orphan", "organization": None},
        "spec": {"project": "shared", "inventory": 7},
    },
    {
        "kind": "Inventory",
        "metadata": {"name": "combined", "organization": "Default"},
        "spec": {
            "kind": "constructed",
            "input_inventories": [
                _PROD,
                {"kind": "Inventory", "name": "edge", "organization": "Ops"},
            ],
            "instance_groups": ["default"],
        },
    },
    {
        "kind": "InventorySource",
        "metadata": {"name": "from-git", "parent": _PROD},
        "spec": {
            "source": "scm",
            "source_project": "playbooks",
            "credential": "aws",
            "execution_environment": "ee",
        },
    },
    {
        "kind": "Group",
        "metadata": {"name": "web", "parent": _PROD},
        "spec": {"hosts": ["web-01", "web-02"], "children": ["web-eu"]},
    },
    {
        "kind": "Group",
        "metadata": {"name": "edge", "parent": {"kind": "Inventory", "name": "edge"}},
        "spec": {"hosts": ["edge-01"]},
    },
    {"kind": "Host", "metadata": {"name": "web-01", "parent": _PROD}, "spec": {"variables": "{}"}},
    {
        "kind": "Schedule",
        "metadata": {
            "name": "nightly",
            "parent": {"kind": "JobTemplate", "name": "deploy", "organization": "Ops"},
        },
        "spec": {"rrule": "x", "inventory": "staging", "execution_environment": "ee"},
    },
    {
        "kind": "Schedule",
        "metadata": {
            "name": "refresh",
            "parent": {"kind": "InventorySource", "name": "from-git", "parent": _PROD},
        },
        "spec": {"rrule": "x", "parent": {"kind": "JobTemplate", "name": "other"}},
    },
    {
        "kind": "WorkflowJobTemplate",
        "metadata": {"name": "release", "organization": "Default"},
        "spec": {
            "inventory": "prod",
            "labels": ["prod", "release"],
            "nodes": [
                {
                    "id": "deploy",
                    "run": {"job_template": "deploy"},
                    "prompts": {"inventory": "staging", "credentials": ["ssh"]},
                }
            ],
        },
    },
    {
        "kind": "Credential",
        "metadata": {"name": "ssh", "organization": "Default"},
        "spec": {"credential_type": "Machine", "inputs": {}},
    },
]

# Recorded from ``prefetch_plan`` before it was rebuilt on ``references_of``.
# Polymorphic parents, integer ids, nulls and workflow nodes add no group.
_RECORDED: dict[str, list[dict[str, str] | None]] = {
    "Credential": [{"organization": "Default"}, {"organization": "Ops"}],
    "ExecutionEnvironment": [None],
    "Project": [None, {"organization": "Default"}],
    "Inventory": [{"organization": "Default"}, {"organization": "Ops"}],
    "Label": [{"organization": "Default"}],
    "InstanceGroup": [None],
    "Host": [{"inventory": "edge"}, {"inventory": "prod", "inventory__organization": "Default"}],
    "Group": [{"inventory": "prod", "inventory__organization": "Default"}],
    "CredentialType": [None],
}


def _groups(plan: dict[str, list[dict[str, str] | None]]) -> dict[str, list[str]]:
    """Each kind's scopes as sorted JSON, since a plan's scope order is a set's."""
    return {
        kind: sorted(json.dumps(scope, sort_keys=True) for scope in scopes)
        for kind, scopes in plan.items()
    }


def test_prefetch_plan_unchanged() -> None:
    docs = [Resource.model_validate(doc) for doc in _BATCH]

    plan = prefetch_plan(docs, catalog=AwxResourceCatalog())

    assert _groups(plan) == _groups(_RECORDED)
