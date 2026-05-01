"""Project: a git/SCM-linked source of playbooks for AWX."""

from __future__ import annotations

from untaped_awx.domain import ActionSpec, FkRef, ResourceSpec

PROJECT_SPEC = ResourceSpec(
    kind="Project",
    cli_name="projects",
    aliases=("proj",),
    api_path="projects",
    identity_keys=("name", "organization"),
    canonical_fields=(
        "description",
        "scm_type",
        "scm_url",
        "scm_branch",
        "scm_refspec",
        "scm_clean",
        "scm_track_submodules",
        "scm_delete_on_update",
        "scm_update_on_launch",
        "scm_update_cache_timeout",
        "allow_override",
        "credential",
        "organization",
        "local_path",
        "timeout",
    ),
    read_only_fields=(
        "id",
        "created",
        "modified",
        "summary_fields",
        "related",
        "type",
        "url",
        "scm_revision",
        "status",
        "last_job_run",
        "last_job_failed",
        "next_job_run",
        "last_update_failed",
        "last_updated",
        "custom_virtualenv",
    ),
    fk_refs=(
        FkRef(field="organization", kind="Organization"),
        FkRef(field="credential", kind="Credential", scope_field="organization"),
    ),
    actions=(ActionSpec(name="update", path="update", returns="job"),),
    list_columns=("name", "organization", "scm_type", "scm_branch", "status"),
    list_filters=("organization",),
    commands=("list", "get", "save", "apply", "update"),
    fidelity="full",
)
