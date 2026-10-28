"""Inventory-scoped source configuration; runtime update state is not restorable."""

from __future__ import annotations

from untaped.sdk import key
from untaped_awx.domain import ActionSpec, FkRef
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.infrastructure.resources.specs._support import UNIVERSAL_READ_ONLY
from untaped_awx.records import InventorySource, SyncOutcome

INVENTORY_SOURCE_SPEC = AwxResourceSpec(
    kind="InventorySource",
    record=InventorySource,
    cli_name="inventory-sources",
    api_path="inventory_sources",
    identity_keys=("name",),
    canonical_fields=(
        "description",
        "source",
        "source_path",
        "source_vars",
        "update_cache_timeout",
        "update_on_launch",
        "overwrite",
        "overwrite_vars",
        "enabled_var",
        "enabled_value",
        "host_filter",
        "limit",
        "verbosity",
        "timeout",
        "source_project",
        "credential",
        "execution_environment",
        "scm_branch",
    ),
    structured_text_fields=("source_vars",),
    read_only_fields=(
        *UNIVERSAL_READ_ONLY,
        "inventory",
        "status",
        "last_updated",
        "last_update_failed",
        "last_job_run",
        "last_job_failed",
        "current_job",
        "current_update",
        "last_update",
        "last_job",
        "next_job_run",
        "has_schedules",
        "can_update",
    ),
    fk_refs=(
        FkRef(field="source_project", kind="Project", scope_field="organization"),
        FkRef(field="credential", kind="Credential", scope_field="organization"),
        FkRef(field="execution_environment", kind="ExecutionEnvironment"),
    ),
    apply_strategy="inventory_child",
    parent_field="inventory",
    actions=(
        ActionSpec(
            name="sync", outcome=SyncOutcome, path="update", returns=frozenset({"inventory_update"})
        ),
    ),
    commands=("list", "get", "save", "apply", "delete"),
    list_columns=(
        key(InventorySource).id,
        key(InventorySource).name,
        "source",
        "status",
        key(InventorySource).inventory,
    ),
)
