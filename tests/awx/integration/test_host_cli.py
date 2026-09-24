"""End-to-end CLI tests for ``untaped awx hosts`` against ``FakeAap``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def _seed_inventory_with_hosts(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    fake.seed(
        "hosts",
        id=101,
        name="web-01",
        inventory=20,
        inventory_name="prod",
        description="frontend",
        enabled=True,
        variables="",
        summary_fields={
            "inventory": {
                "id": 20,
                "name": "prod",
                "organization_id": 1,
                "organization_name": "Default",
            }
        },
    )
    fake.seed(
        "hosts",
        id=102,
        name="api-01",
        inventory=20,
        inventory_name="prod",
        description="api",
        enabled=False,
        variables="",
        summary_fields={
            "inventory": {
                "id": 20,
                "name": "prod",
                "organization_id": 1,
                "organization_name": "Default",
            }
        },
    )


def test_hosts_apply_creates_host_via_nested_endpoint(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Apply a Host doc — strategy POSTs to ``/inventories/<id>/hosts/``."""
    seeded_default_org.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    doc = tmp_path / "host.yml"
    doc.write_text(
        """
        kind: Host
        apiVersion: untaped.dev/awx/v1
        metadata:
          name: web-01
          parent:
            kind: Inventory
            name: prod
            organization: Default
        spec:
          description: Frontend web server
          enabled: true
        """
    )
    result = CliInvoker().invoke(app, ["hosts", "apply", str(doc), "--yes"])
    assert result.exit_code == 0, result.output
    # The fake's nested POST handler stores the host with inventory=20.
    hosts = list(seeded_default_org.store["hosts"].values())
    assert len(hosts) == 1
    assert hosts[0]["name"] == "web-01"
    assert hosts[0]["inventory"] == 20
    assert hosts[0]["description"] == "Frontend web server"


def test_hosts_apply_preview_does_not_write(seeded_default_org: Any, tmp_path: Path) -> None:
    seeded_default_org.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    doc = tmp_path / "host.yml"
    doc.write_text(
        """
        kind: Host
        metadata:
          name: web-01
          parent:
            kind: Inventory
            name: prod
            organization: Default
        spec:
          description: Frontend web server
        """
    )
    result = CliInvoker().invoke(app, ["hosts", "apply", "--dry-run", str(doc)])
    assert result.exit_code == 0, result.output
    assert seeded_default_org.store["hosts"] == {}


def test_hosts_save_emits_metadata_parent_inventory(fake_aap: Any) -> None:
    """Critical for round-trip: a saved Host must include
    ``metadata.parent.kind: Inventory`` so applying it back through
    ``InventoryChildApplyStrategy`` succeeds. The strategy rejects with
    ``identity missing 'parent'`` otherwise — silent restore breakage."""
    _seed_inventory_with_hosts(fake_aap)
    result = CliInvoker().invoke(app, ["hosts", "export", "web-01"])
    assert result.exit_code == 0, result.output
    import yaml as _yaml

    parsed = _yaml.safe_load(result.stdout)
    assert parsed["kind"] == "Host"
    assert parsed["metadata"]["name"] == "web-01"
    assert parsed["metadata"]["parent"]["kind"] == "Inventory"
    assert parsed["metadata"]["parent"]["name"] == "prod"
    assert parsed["metadata"]["parent"]["organization"] == "Default"


def test_hosts_save_round_trips_through_apply(fake_aap: Any, tmp_path: Path) -> None:
    """Save → apply round-trip: a saved Host must reapply cleanly with
    ``unchanged`` (or at worst no diff) against the same AWX state."""
    _seed_inventory_with_hosts(fake_aap)
    save_result = CliInvoker().invoke(app, ["hosts", "export", "web-01"])
    assert save_result.exit_code == 0, save_result.output
    saved = tmp_path / "host.yml"
    saved.write_text(save_result.stdout)
    # Apply with --yes so we'd error loudly if metadata.parent were missing.
    apply_result = CliInvoker().invoke(app, ["hosts", "apply", str(saved), "--yes"])
    assert apply_result.exit_code == 0, apply_result.output
    # The host already exists with the same body — should be unchanged.
    assert "unchanged" in apply_result.output


def test_hosts_list_with_names_resolves_inventory(fake_aap: Any) -> None:
    """Host's ``inventory`` is in ``read_only_fields`` (FK identity comes
    from ``metadata.parent``), so ``--with-names`` previously couldn't
    flatten it. The ``flatten_fks`` columns= extension fixes that: the
    inventory column now renders the name from ``summary_fields``."""
    _seed_inventory_with_hosts(fake_aap)
    result = CliInvoker().invoke(
        app,
        ["hosts", "list", "--with-names", "--columns", "inventory", "--format", "raw"],
    )
    assert result.exit_code == 0, result.output
    rows = sorted(result.stdout.strip().splitlines())
    # Both seeded hosts live in inventory id=20 named "prod" — flatten_fks
    # turns the bare id into the human-readable name.
    assert rows == ["prod", "prod"]


def test_hosts_get_with_inventory_scope_disambiguates_across_inventories(
    seeded_default_org: Any,
) -> None:
    """Two inventories with the same host name → ``--inventory`` picks the
    right one. Without the flag, name lookup is global (first match wins),
    which is ambiguous."""
    seeded_default_org.seed(
        "inventories", id=20, name="prod", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "inventories", id=21, name="staging", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "hosts",
        id=101,
        name="web-01",
        inventory=20,
        inventory_name="prod",
        description="prod web",
    )
    seeded_default_org.seed(
        "hosts",
        id=102,
        name="web-01",
        inventory=21,
        inventory_name="staging",
        description="staging web",
    )
    result = CliInvoker().invoke(
        app,
        [
            "hosts",
            "get",
            "web-01",
            "--inventory",
            "staging",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "102"


def test_hosts_get_with_inventory_organization_disambiguates_across_orgs(
    seeded_default_org: Any,
) -> None:
    """Same inventory name in two orgs → ``--inventory-organization`` picks."""
    seeded_default_org.seed("organizations", id=2, name="Other")
    seeded_default_org.seed(
        "inventories", id=20, name="prod", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "inventories", id=21, name="prod", organization=2, organization_name="Other"
    )
    seeded_default_org.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    seeded_default_org.seed("hosts", id=102, name="web-01", inventory=21, inventory_name="prod")
    result = CliInvoker().invoke(
        app,
        [
            "hosts",
            "get",
            "web-01",
            "--inventory",
            "prod",
            "--inventory-organization",
            "Other",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "102"


def test_hosts_get_accepts_inventory_org_alias(seeded_default_org: Any) -> None:
    """``--inventory-org`` matches the full ``--inventory-organization`` spelling."""
    seeded_default_org.seed("organizations", id=2, name="Other")
    seeded_default_org.seed(
        "inventories", id=20, name="prod", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "inventories", id=21, name="prod", organization=2, organization_name="Other"
    )
    seeded_default_org.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    seeded_default_org.seed("hosts", id=102, name="web-01", inventory=21, inventory_name="prod")
    result = CliInvoker().invoke(
        app,
        [
            "hosts",
            "get",
            "web-01",
            "--inventory",
            "prod",
            "--inventory-org",
            "Other",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "102"
