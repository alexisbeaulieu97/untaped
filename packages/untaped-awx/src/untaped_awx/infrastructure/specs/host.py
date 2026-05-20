"""Host: a single inventory entry, scoped to an Inventory parent.

Identity is ``(name, parent)`` where ``parent`` is an :class:`IdentityRef`
to an Inventory — same pattern Schedule uses for its (polymorphic)
parent. Writes go through ``InventoryChildApplyStrategy`` which POSTs to
``/inventories/<id>/hosts/`` so the ``inventory`` FK is implied by the
URL rather than carried in the body.
"""

from __future__ import annotations

from untaped_awx.infrastructure.spec import AwxResourceSpec

HOST_SPEC = AwxResourceSpec(
    kind="Host",
    cli_name="hosts",
    api_path="hosts",
    identity_keys=("name",),  # unique within parent (Inventory)
    canonical_fields=("description", "enabled", "instance_id", "variables"),
    read_only_fields=(
        "id",
        "inventory",
        "created",
        "modified",
        "summary_fields",
        "related",
        "type",
        "url",
        "has_active_failures",
        "has_inventory_sources",
        "last_job",
        "last_job_host_summary",
        "ansible_facts_modified",
    ),
    list_columns=("id", "name", "inventory", "enabled"),
    commands=("list", "get", "save", "apply", "delete"),
    apply_strategy="inventory_child",
    fidelity="full",
)
