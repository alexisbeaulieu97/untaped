"""End-to-end CLI tests for ``untaped awx hosts`` against ``FakeAap``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from untaped_awx import app

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


def test_hosts_list_returns_seeded_records(fake_aap: Any) -> None:
    _seed_inventory_with_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        ["hosts", "list", "--format", "raw", "--columns", "name"],
    )
    assert result.exit_code == 0, result.output
    names = sorted(result.stdout.strip().splitlines())
    assert names == ["api-01", "web-01"]


def test_hosts_list_filter_passes_through(fake_aap: Any) -> None:
    _seed_inventory_with_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "hosts",
            "list",
            "--filter",
            "inventory__name=prod",
            "--filter",
            "name__icontains=web",
            "--format",
            "raw",
            "--columns",
            "name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "web-01"


def test_hosts_get_by_id(fake_aap: Any) -> None:
    _seed_inventory_with_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        ["hosts", "get", "101", "--format", "json", "--columns", "name"],
    )
    assert result.exit_code == 0, result.output
    assert "web-01" in result.stdout


def test_hosts_get_by_stdin(fake_aap: Any) -> None:
    _seed_inventory_with_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        ["hosts", "get", "--stdin", "--format", "raw", "--columns", "name"],
        input="101\n102\n",
    )
    assert result.exit_code == 0, result.output
    names = result.stdout.strip().splitlines()
    assert sorted(names) == ["api-01", "web-01"]


def test_hosts_list_dotted_columns_walks_summary_fields(fake_aap: Any) -> None:
    """``--columns summary_fields.inventory.name`` walks the dict tree."""
    _seed_inventory_with_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "hosts",
            "list",
            "--format",
            "raw",
            "--columns",
            "name",
            "--columns",
            "summary_fields.inventory.name",
        ],
    )
    assert result.exit_code == 0, result.output
    # raw with two columns is tab-separated
    rows = sorted(result.stdout.strip().splitlines())
    assert rows == ["api-01\tprod", "web-01\tprod"]


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
    result = CliRunner().invoke(app, ["hosts", "apply", "--file", str(doc), "--yes"])
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
    result = CliRunner().invoke(app, ["hosts", "apply", "--file", str(doc)])
    assert result.exit_code == 0, result.output
    assert seeded_default_org.store["hosts"] == {}


def test_hosts_save_round_trips_to_yaml(fake_aap: Any) -> None:
    _seed_inventory_with_hosts(fake_aap)
    result = CliRunner().invoke(app, ["hosts", "save", "web-01"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    # Save dumps YAML — exact field ordering varies, but kind + name must appear.
    assert "kind: Host" in out
    assert "web-01" in out


def test_hosts_save_emits_metadata_parent_inventory(fake_aap: Any) -> None:
    """Critical for round-trip: a saved Host must include
    ``metadata.parent.kind: Inventory`` so applying it back through
    ``InventoryChildApplyStrategy`` succeeds. The strategy rejects with
    ``identity missing 'parent'`` otherwise — silent restore breakage."""
    _seed_inventory_with_hosts(fake_aap)
    result = CliRunner().invoke(app, ["hosts", "save", "web-01"])
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
    save_result = CliRunner().invoke(app, ["hosts", "save", "web-01"])
    assert save_result.exit_code == 0, save_result.output
    saved = tmp_path / "host.yml"
    saved.write_text(save_result.stdout)
    # Apply with --yes so we'd error loudly if metadata.parent were missing.
    apply_result = CliRunner().invoke(app, ["hosts", "apply", "--file", str(saved), "--yes"])
    assert apply_result.exit_code == 0, apply_result.output
    # The host already exists with the same body — should be unchanged.
    assert "unchanged" in apply_result.output


def test_hosts_list_with_names_resolves_inventory(fake_aap: Any) -> None:
    """Host's ``inventory`` is in ``read_only_fields`` (FK identity comes
    from ``metadata.parent``), so ``--with-names`` previously couldn't
    flatten it. The ``flatten_fks`` columns= extension fixes that: the
    inventory column now renders the name from ``summary_fields``."""
    _seed_inventory_with_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        ["hosts", "list", "--with-names", "--columns", "inventory", "--format", "raw"],
    )
    assert result.exit_code == 0, result.output
    rows = sorted(result.stdout.strip().splitlines())
    # Both seeded hosts live in inventory id=20 named "prod" — flatten_fks
    # turns the bare id into the human-readable name.
    assert rows == ["prod", "prod"]


def test_hosts_list_default_columns_no_dotted_summary_path(fake_aap: Any) -> None:
    """Default-columns audit: Host's default projection is the consistent
    ``name, inventory, enabled`` triple — no ``summary_fields.*`` paths.
    Pinning this so a future spec edit doesn't silently regress to dotted
    headers."""
    from untaped_awx.infrastructure.specs import HOST_SPEC

    assert HOST_SPEC.list_columns == ("id", "name", "inventory", "enabled")
    for col in HOST_SPEC.list_columns:
        assert "." not in col, f"dotted path {col!r} leaked into default columns"


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
    result = CliRunner().invoke(
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
    result = CliRunner().invoke(
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
