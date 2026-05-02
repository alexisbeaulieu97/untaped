"""Schedule: cron-like trigger attached to a launchable parent.

The parent FK is polymorphic — a schedule can attach to a JobTemplate,
WorkflowJobTemplate, Project, or InventorySource. The ``schedule``
apply strategy POSTs against ``<parent_path>/<parent_id>/schedules/``
for create and PATCHes the global ``/schedules/<id>/`` for update.
"""

from __future__ import annotations

from untaped_awx.domain import FkRef, ResourceSpec

SCHEDULE_SPEC = ResourceSpec(
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
        "job_tags",
        "skip_tags",
        "limit",
        "diff_mode",
        "verbosity",
        "forks",
        "job_slice_count",
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
    ),
    apply_strategy="schedule",
    list_columns=("name", "next_run", "rrule", "enabled"),
    commands=("list", "get", "save", "apply"),
    fidelity="full",
)
