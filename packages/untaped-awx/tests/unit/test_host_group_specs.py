"""Spec invariants for the Host and Group resource kinds."""

from __future__ import annotations

from untaped_awx.infrastructure.specs import ALL_SPECS, GROUP_SPEC, HOST_SPEC


def test_host_spec_basics() -> None:
    assert HOST_SPEC.kind == "Host"
    assert HOST_SPEC.cli_name == "hosts"
    assert HOST_SPEC.api_path == "hosts"
    assert HOST_SPEC.identity_keys == ("name",)
    assert HOST_SPEC.fidelity == "full"
    assert HOST_SPEC.apply_strategy == "inventory_child"
    assert HOST_SPEC.commands == ("list", "get", "save", "apply", "delete")
    # ``inventory`` is on every GET response but the apply strategy injects
    # it via the URL — never as a body field — so it must stay out of
    # canonical_fields and thus out of write payloads.
    assert "inventory" not in HOST_SPEC.canonical_fields
    assert "inventory" in HOST_SPEC.read_only_fields


def test_host_spec_has_no_fk_refs() -> None:
    # Inventory parent is metadata.parent (Schedule pattern), not a body FK.
    assert HOST_SPEC.fk_refs == ()


def test_group_spec_basics() -> None:
    assert GROUP_SPEC.kind == "Group"
    assert GROUP_SPEC.cli_name == "groups"
    assert GROUP_SPEC.api_path == "groups"
    assert GROUP_SPEC.identity_keys == ("name",)
    assert GROUP_SPEC.fidelity == "full"
    assert GROUP_SPEC.apply_strategy == "inventory_child"
    assert GROUP_SPEC.commands == ("list", "get", "save", "apply", "delete")
    assert "inventory" not in GROUP_SPEC.canonical_fields
    assert "inventory" in GROUP_SPEC.read_only_fields


def test_group_membership_fk_refs_use_sub_endpoints() -> None:
    """``hosts`` and ``children`` are managed via associate/disassociate POSTs."""
    fields = {ref.field: ref for ref in GROUP_SPEC.fk_refs}
    assert "hosts" in fields and "children" in fields
    for ref in fields.values():
        assert ref.multi is True
        assert ref.sub_endpoint is not None
        assert ref.scope_field == "inventory"
    assert fields["hosts"].kind == "Host"
    assert fields["children"].kind == "Group"


def test_host_and_group_registered_in_catalog() -> None:
    kinds = {s.kind for s in ALL_SPECS}
    assert "Host" in kinds and "Group" in kinds
