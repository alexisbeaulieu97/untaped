"""Job Template: an Ansible playbook configured to run with specific
project/inventory/credentials. Headline kind for ``launch``."""

from __future__ import annotations

from untaped.capabilities.awx.domain import ActionSpec, FkRef
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capabilities.awx.infrastructure.specs._support import UNIVERSAL_READ_ONLY

JOB_TEMPLATE_SPEC = AwxResourceSpec(
    kind="JobTemplate",
    cli_name="job-templates",
    api_path="job_templates",
    structured_text_fields=("extra_vars",),
    identity_keys=("name", "organization"),
    canonical_fields=(
        "description",
        "job_type",
        "playbook",
        "scm_branch",
        "forks",
        "limit",
        "verbosity",
        "extra_vars",
        "job_tags",
        "force_handlers",
        "skip_tags",
        "start_at_task",
        "timeout",
        "use_fact_cache",
        "host_config_key",
        "ask_scm_branch_on_launch",
        "ask_diff_mode_on_launch",
        "ask_variables_on_launch",
        "ask_limit_on_launch",
        "ask_tags_on_launch",
        "ask_skip_tags_on_launch",
        "ask_job_type_on_launch",
        "ask_verbosity_on_launch",
        "ask_inventory_on_launch",
        "ask_credential_on_launch",
        "ask_execution_environment_on_launch",
        "ask_labels_on_launch",
        "ask_forks_on_launch",
        "ask_job_slice_count_on_launch",
        "ask_timeout_on_launch",
        "ask_instance_groups_on_launch",
        "survey_enabled",
        "survey_spec",
        "become_enabled",
        "diff_mode",
        "allow_simultaneous",
        "custom_virtualenv",
        "job_slice_count",
        "webhook_service",
        "webhook_credential",
        "webhook_key",
        "prevent_instance_group_fallback",
        "execution_environment",
        "organization",
        "project",
        "inventory",
        "credentials",
        "labels",
    ),
    read_only_fields=(
        *UNIVERSAL_READ_ONLY,
        "last_job_run",
        "last_job_failed",
        "last_job_status",
        "next_job_run",
        "status",
    ),
    fk_refs=(
        FkRef(field="organization", kind="Organization"),
        FkRef(field="project", kind="Project", scope_field="organization"),
        FkRef(field="inventory", kind="Inventory", scope_field="organization"),
        FkRef(
            field="webhook_credential",
            kind="Credential",
            scope_field="organization",
        ),
        FkRef(field="execution_environment", kind="ExecutionEnvironment"),
        FkRef(
            field="credentials",
            kind="Credential",
            scope_field="organization",
            multi=True,
            sub_endpoint="credentials",
        ),
        FkRef(
            field="labels",
            kind="Label",
            scope_field="organization",
            multi=True,
            sub_endpoint="labels",
        ),
    ),
    launch_fk_refs=(
        FkRef(field="execution_environment", kind="ExecutionEnvironment"),
        FkRef(field="labels", kind="Label", scope_field="organization", multi=True),
        FkRef(field="instance_groups", kind="InstanceGroup", multi=True),
    ),
    sub_document_fields=("survey_spec",),
    secret_paths=("webhook_key", "survey_spec.spec.*[type=password].default"),
    optional_secret_paths=("survey_spec.spec.*[type=password].default",),
    # AWX may normalize and enrich the submitted survey document while
    # retaining the user-owned questions/defaults.  Batch verification allows
    # that explicitly instead of silently weakening comparison for all fields.
    server_enriched_fields=("survey_spec",),
    actions=(
        # ``accepts`` is the public CLI contract: each name listed here
        # gets a CLI flag wired in `_add_launch`. The CLI dispatches
        # on membership so every kind's launch surface stays honest.
        ActionSpec(
            name="launch",
            path="launch",
            returns=frozenset({"job", "workflow_job"}),
            accepts=frozenset(
                {
                    "extra_vars",
                    "limit",
                    "inventory",
                    "credentials",
                    "scm_branch",
                    "job_tags",
                    "skip_tags",
                    "verbosity",
                    "diff_mode",
                    "job_type",
                }
            ),
        ),
    ),
    list_columns=("id", "name"),
    commands=("list", "get", "save", "apply", "delete", "copy", "rename"),
    fidelity="full",
)
