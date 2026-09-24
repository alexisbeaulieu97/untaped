"""Schedule: cron-like trigger attached to a launchable parent.

The parent FK is polymorphic — a schedule can attach to a JobTemplate,
WorkflowJobTemplate, Project, or InventorySource. The ``schedule``
apply strategy POSTs against ``<parent_path>/<parent_id>/schedules/``
for create and PATCHes the global ``/schedules/<id>/`` for update.
"""

from __future__ import annotations

from untaped.capabilities.awx.domain import FkRef
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capabilities.awx.infrastructure.specs._support import UNIVERSAL_READ_ONLY

SCHEDULE_SPEC = AwxResourceSpec(
    kind="Schedule",
    cli_name="schedules",
    api_path="schedules",
    identity_keys=("name",),  # uniqueness is name-within-parent; parent is in metadata.parent
    canonical_fields=(
        "description",
        "rrule",
        "enabled",
        "extra_data",
        "inventory",
        "scm_branch",
        "job_type",
        "job_tags",
        "skip_tags",
        "limit",
        "diff_mode",
        "verbosity",
        "forks",
        "job_slice_count",
        "timeout",
        "execution_environment",
    ),
    read_only_fields=(
        *UNIVERSAL_READ_ONLY,
        "last_run",
        "next_run",
        "dtstart",
        "dtend",
        "timezone",
        "until",
    ),
    fk_refs=(
        FkRef(
            field="parent",
            polymorphic=True,
            kind_in_value="kind",
            scope_field_in_value="organization",
        ),
        # Schedules can override the parent's inventory; org-scoped.
        FkRef(field="inventory", kind="Inventory", scope_field="organization"),
        FkRef(field="execution_environment", kind="ExecutionEnvironment"),
    ),
    apply_strategy="schedule",
    parent_field="unified_job_template",
    list_columns=("id", "name", "last_run", "next_run", "enabled"),
    commands=("list", "get", "save", "apply", "delete"),
    fidelity="full",
)
