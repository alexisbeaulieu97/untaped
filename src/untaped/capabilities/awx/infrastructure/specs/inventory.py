"""Inventory lifecycle, constructed proxy fields and ordered relationships."""

from untaped.capabilities.awx.domain import ActionSpec, FkRef
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec

INVENTORY_SPEC = AwxResourceSpec(
    kind="Inventory",
    cli_name="inventories",
    api_path="inventories",
    relationship_api_path="inventories",
    identity_keys=("name", "organization"),
    canonical_fields=(
        "description",
        "kind",
        "host_filter",
        "variables",
        "prevent_instance_group_fallback",
        "source_vars",
        "update_cache_timeout",
        "limit",
        "verbosity",
    ),
    structured_text_fields=("variables", "source_vars"),
    apply_strategy="inventory",
    read_only_fields=(
        "id",
        "created",
        "modified",
        "summary_fields",
        "related",
        "type",
        "url",
        "total_hosts",
        "hosts_with_active_failures",
        "total_groups",
        "has_active_failures",
        "has_inventory_sources",
        "total_inventory_sources",
        "inventory_sources_with_failures",
        "pending_deletion",
    ),
    fk_refs=(
        FkRef(field="organization", kind="Organization"),
        FkRef(
            field="input_inventories",
            kind="Inventory",
            multi=True,
            sub_endpoint="input_inventories",
            ordered=True,
        ),
        FkRef(
            field="instance_groups",
            kind="InstanceGroup",
            multi=True,
            sub_endpoint="instance_groups",
            ordered=True,
        ),
    ),
    actions=(ActionSpec(name="sync", path=None, returns="inventory_update"),),
    list_columns=("id", "name", "organization", "total_hosts"),
    commands=("list", "get", "save", "apply", "patch", "edit", "delete"),
)
