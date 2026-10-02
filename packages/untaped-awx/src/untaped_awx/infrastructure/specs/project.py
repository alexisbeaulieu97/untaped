"""Project: a git/SCM-linked source of playbooks for AWX."""

from __future__ import annotations

from untaped_awx.domain import ActionSpec, DerivedField, FkRef
from untaped_awx.infrastructure.spec import AwxResourceSpec
from untaped_awx.infrastructure.specs._support import UNIVERSAL_READ_ONLY

PROJECT_SPEC = AwxResourceSpec(
    kind="Project",
    cli_name="projects",
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
        "signature_validation_credential",
        "default_environment",
        "local_path",
        "timeout",
    ),
    read_only_fields=(
        *UNIVERSAL_READ_ONLY,
        "scm_revision",
        "status",
        "last_job_run",
        "last_job_failed",
        "next_job_run",
        "last_update_failed",
        "last_updated",
        "custom_virtualenv",
    ),
    derived_fields=(DerivedField(field="local_path", unless_empty="scm_type"),),
    fk_refs=(
        FkRef(field="organization", kind="Organization"),
        FkRef(field="credential", kind="Credential", scope_field="organization"),
        FkRef(
            field="signature_validation_credential",
            kind="Credential",
            scope_field="organization",
        ),
        # The project's default execution environment (global, no org scope).
        FkRef(field="default_environment", kind="ExecutionEnvironment"),
    ),
    actions=(ActionSpec(name="sync", path="update", returns=frozenset({"project_update"})),),
    list_columns=("id", "name", "status"),
    commands=("list", "get", "save", "apply", "delete"),
    fidelity="full",
)
