"""Catalog lookups and the invariants every AwxResourceSpec must keep."""

from __future__ import annotations

import pytest

from untaped.capabilities.awx.infrastructure import AwxResourceCatalog
from untaped.capabilities.awx.infrastructure.specs import ALL_SPECS, UNIVERSAL_READ_ONLY
from untaped.capability_api import ConfigError


def test_catalog_resolves_kinds_and_cli_names() -> None:
    cat = AwxResourceCatalog()
    assert cat.get("JobTemplate").cli_name == "job-templates"
    assert cat.by_cli_name("job-templates").kind == "JobTemplate"
    assert set(cat.kinds()) == {spec.kind for spec in ALL_SPECS}
    with pytest.raises(ConfigError, match="JobTemplate"):  # the message lists known kinds
        cat.get("NotARealKind")


def test_apply_multi_fk_refs_have_sub_endpoints() -> None:
    """Apply-time multi-FKs must reconcile through explicit sub-endpoints.

    Launch-only multi-FKs are intentionally out of scope: fields like
    JobTemplate.labels are launch payload overrides, not resource-body state.
    """
    jt_launch_fields = {
        fk.field: fk for fk in AwxResourceCatalog().get("JobTemplate").launch_fk_refs
    }
    assert jt_launch_fields["labels"].multi and jt_launch_fields["labels"].sub_endpoint is None

    missing = [
        f"{spec.kind}.{ref.field}"
        for spec in ALL_SPECS
        if "apply" in spec.commands
        for ref in spec.fk_refs
        if ref.multi and ref.sub_endpoint is None
    ]
    assert not missing


def test_specs_without_apply_command_are_read_only() -> None:
    """Every spec opting out of apply via ``commands`` must also be ``read_only``.

    ``application/apply_resource`` gates on ``fidelity == "read_only"`` only —
    if a future spec sets ``commands=("list", "get")`` but ``fidelity="full"``,
    the apply use case would silently issue create/update calls. The CLI-level
    gate (per-kind sub-apps hide ``apply``) is independent, but
    ``untaped awx apply <file>`` flows through the use case directly.
    """
    for spec in ALL_SPECS:
        if "apply" not in spec.commands:
            assert spec.fidelity == "read_only", (
                f"{spec.kind}: 'apply' not in commands but fidelity={spec.fidelity!r}. "
                f"Either add 'apply' to commands or set fidelity='read_only', "
                f"otherwise `untaped awx apply <file>` will issue writes."
            )


def test_non_read_only_specs_expose_save_command() -> None:
    """Top-level ``awx save`` uses fidelity to decide saveability.

    If a future writable spec omitted the per-kind ``save`` command, the
    bulk and per-kind save surfaces would drift. Keep those contracts
    aligned at the spec layer rather than making application code read
    CLI-only ``commands``.
    """
    missing = [
        spec.kind
        for spec in ALL_SPECS
        if spec.fidelity != "read_only" and "save" not in spec.commands
    ]
    assert not missing


def test_saveable_list_columns_are_domain_known_filter_fields() -> None:
    """Bulk-save filter validation runs in application code using only
    domain ``ResourceSpec`` fields. Any displayed list column that users
    reasonably filter on must therefore also be represented by the
    domain-known field set.
    """
    missing: list[str] = []
    for spec in ALL_SPECS:
        if spec.fidelity == "read_only":
            continue
        missing.extend(
            f"{spec.kind}.{column}"
            for column in spec.list_columns
            if column.split("__", 1)[0] not in spec.known_fields
        )

    assert not missing


def test_mutable_specs_declare_universal_read_only_fields() -> None:
    """Read-only stripping in ``ApplyPlanner.plan_payload`` is the safety net
    under the passthrough model: every mutable spec must declare the
    server-managed ``UNIVERSAL_READ_ONLY`` fields so they're never PATCHed back
    (e.g. a stray ``id``/``summary_fields`` from a get-export)."""
    missing: list[str] = []
    for spec in ALL_SPECS:
        if spec.fidelity == "read_only":
            continue
        declared = set(spec.read_only_fields)
        missing.extend(
            f"{spec.kind}.{field}" for field in UNIVERSAL_READ_ONLY if field not in declared
        )

    assert not missing, f"mutable specs missing universal read-only fields: {missing}"


def test_parent_owned_specs_declare_their_parent_field() -> None:
    """Parent-owned write strategies read the parent ID from ``parent_field``."""
    parents = {spec.kind: spec.parent_field for spec in ALL_SPECS}
    assert parents["Host"] == parents["Group"] == parents["InventorySource"] == "inventory"
    assert parents["Schedule"] == "unified_job_template"
    for spec in ALL_SPECS:
        if spec.apply_strategy not in {"inventory_child", "schedule"}:
            assert spec.parent_field is None, spec.kind


def test_inventory_child_kinds_match_specs() -> None:
    """Identity references scope children by inventory; keep that set in step."""
    from untaped.capabilities.awx.domain.inventory import INVENTORY_CHILD_KINDS

    children = {spec.kind for spec in ALL_SPECS if spec.parent_field == "inventory"}
    assert children == INVENTORY_CHILD_KINDS


def test_only_inventory_deletes_asynchronously_and_expands_sync() -> None:
    assert {spec.kind for spec in ALL_SPECS if spec.async_delete} == {"Inventory"}
    expanding = {
        (spec.kind, action.name, action.expand_to)
        for spec in ALL_SPECS
        for action in spec.actions
        if action.expand_to is not None
    }
    assert expanding == {("Inventory", "sync", "InventorySource")}


def test_immutable_fields_cover_identity_and_ancestry() -> None:
    cat = AwxResourceCatalog()
    host = cat.get("Host").immutable_fields
    assert {"id", "kind", "type", "name", "organization", "parent", "inventory"} <= host
    assert "inventory" not in cat.get("JobTemplate").immutable_fields
    assert "description" not in host
