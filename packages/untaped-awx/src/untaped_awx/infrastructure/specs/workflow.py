"""Workflow Job Template: header only in v0.

The node graph + edges are intentionally not roundtripped. Saved files
include a YAML comment noting the omission. Adding sub-resource +
``apply_hooks.apply_workflow_nodes`` is the v0.5 milestone.
"""

from __future__ import annotations

from untaped_awx.domain import ActionSpec, FkRef
from untaped_awx.infrastructure.spec import AwxResourceSpec

WORKFLOW_JOB_TEMPLATE_SPEC = AwxResourceSpec(
    kind="WorkflowJobTemplate",
    cli_name="workflow-templates",
    api_path="workflow_job_templates",
    identity_keys=("name", "organization"),
    canonical_fields=(
        "description",
        "extra_vars",
        "organization",
        "inventory",
        "scm_branch",
        "limit",
        "allow_simultaneous",
        "ask_variables_on_launch",
        "ask_inventory_on_launch",
        "ask_scm_branch_on_launch",
        "ask_limit_on_launch",
        "ask_labels_on_launch",
        "ask_skip_tags_on_launch",
        "ask_tags_on_launch",
        "survey_enabled",
        "survey_spec",
        "webhook_service",
        "webhook_credential",
        "webhook_key",
    ),
    read_only_fields=(
        "id",
        "created",
        "modified",
        "summary_fields",
        "related",
        "type",
        "url",
        "last_job_run",
        "last_job_failed",
        "next_job_run",
        "status",
    ),
    fk_refs=(
        FkRef(field="organization", kind="Organization"),
        FkRef(field="inventory", kind="Inventory", scope_field="organization"),
    ),
    secret_paths=("webhook_key", "survey_spec.spec.*.default"),
    actions=(
        ActionSpec(
            name="launch",
            path="launch",
            returns="job",
            accepts=frozenset(
                {
                    "extra_vars",
                    "limit",
                    "inventory",
                    "scm_branch",
                    "job_tags",
                    "skip_tags",
                }
            ),
        ),
    ),
    list_columns=("name", "organization", "last_job_status"),
    commands=("list", "get", "save", "apply", "launch"),
    fidelity="partial",
    fidelity_note="node graph + edges not roundtripped (v0 limitation)",
)
