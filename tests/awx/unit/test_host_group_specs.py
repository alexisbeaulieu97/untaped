"""Spec invariants for the Host and Group resource kinds."""

from __future__ import annotations

import pytest

from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capabilities.awx.infrastructure.specs import GROUP_SPEC, HOST_SPEC


@pytest.mark.parametrize("spec", [HOST_SPEC, GROUP_SPEC], ids=["Host", "Group"])
def test_inventory_is_never_a_body_field(spec: AwxResourceSpec) -> None:
    """The parent inventory travels in the URL (``metadata.parent``), never the body."""
    assert spec.apply_strategy == "inventory_child"
    assert "inventory" not in spec.canonical_fields
    assert "inventory" in spec.read_only_fields
    assert "inventory" not in {ref.field for ref in spec.fk_refs}


def test_group_membership_fk_refs_are_inventory_scoped() -> None:
    """``hosts`` and ``children`` are managed via associate/disassociate POSTs."""
    refs = {ref.field: (ref.kind, ref.scope_field) for ref in GROUP_SPEC.fk_refs}
    assert refs == {"hosts": ("Host", "inventory"), "children": ("Group", "inventory")}
