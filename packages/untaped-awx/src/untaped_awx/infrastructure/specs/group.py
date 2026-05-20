"""Group: a logical bucket of hosts (and child groups) within an Inventory.

Identity is ``(name, parent)`` where ``parent`` is an :class:`IdentityRef`
to an Inventory. Membership is **declared in the spec body** as lists of
names (``hosts:``, ``children:``) and reconciled by the apply pipeline
via associate/disassociate POSTs against
``/groups/<id>/hosts/`` and ``/groups/<id>/children/`` — the AWX REST
convention for many-to-many edges.

``parents`` is intentionally *not* declared as a writable FK ref:
hierarchy is expressed top-down via each parent's ``children`` list, so
declaring both directions would produce ambiguous round-trips.
"""

from __future__ import annotations

from untaped_awx.domain import FkRef
from untaped_awx.infrastructure.spec import AwxResourceSpec

GROUP_SPEC = AwxResourceSpec(
    kind="Group",
    cli_name="groups",
    api_path="groups",
    identity_keys=("name",),  # unique within parent (Inventory)
    canonical_fields=("description", "variables"),
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
        "total_hosts",
        "hosts_with_active_failures",
        "total_groups",
        "groups_with_active_failures",
        "has_inventory_sources",
    ),
    fk_refs=(
        FkRef(
            field="hosts",
            kind="Host",
            multi=True,
            sub_endpoint="hosts",
            scope_field="inventory",
        ),
        FkRef(
            field="children",
            kind="Group",
            multi=True,
            sub_endpoint="children",
            scope_field="inventory",
        ),
    ),
    list_columns=("id", "name", "description"),
    commands=("list", "get", "save", "apply", "delete"),
    apply_strategy="inventory_child",
    fidelity="full",
)
