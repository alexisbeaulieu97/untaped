"""FK-only specs: looked up via :class:`FkResolver` but never CRUD'd in v0.

These specs declare just enough for ``name → id`` resolution and
``list/get`` browsing. They omit save/apply because the user explicitly
scoped them out of v0.
"""

from __future__ import annotations

from untaped_awx.domain import FkRef
from untaped_awx.infrastructure.spec import AwxResourceSpec

ORGANIZATION_SPEC = AwxResourceSpec(
    kind="Organization",
    cli_name="organizations",
    api_path="organizations",
    identity_keys=("name",),
    canonical_fields=("description",),
    read_only_fields=(
        "id",
        "created",
        "modified",
        "summary_fields",
        "related",
        "type",
        "url",
    ),
    list_columns=("id", "name", "description"),
    commands=("list", "get"),
    fidelity="read_only",
    fidelity_note="organization CRUD is out of v0 scope",
)


INVENTORY_SPEC = AwxResourceSpec(
    kind="Inventory",
    cli_name="inventories",
    api_path="inventories",
    identity_keys=("name", "organization"),
    canonical_fields=("description", "kind", "host_filter", "variables"),
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
    fk_refs=(FkRef(field="organization", kind="Organization"),),
    list_columns=("name", "organization", "kind", "total_hosts"),
    commands=("list", "get"),
    fidelity="read_only",
    fidelity_note="inventory CRUD is out of v0 scope",
)


CREDENTIAL_TYPE_SPEC = AwxResourceSpec(
    kind="CredentialType",
    cli_name="credential-types",
    api_path="credential_types",
    identity_keys=("name",),  # CredentialTypes are global (no organization)
    canonical_fields=("description", "kind", "inputs", "injectors"),
    read_only_fields=(
        "id",
        "created",
        "modified",
        "summary_fields",
        "related",
        "type",
        "url",
        "managed",
        "namespace",
    ),
    list_columns=("id", "name", "kind"),
    commands=("list", "get"),
    fidelity="read_only",
    fidelity_note="credential type CRUD is out of v0 scope",
)
