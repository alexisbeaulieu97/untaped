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
        summary_fields={"inventory": {"id": 20, "name": "prod"}},
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
        summary_fields={"inventory": {"id": 20, "name": "prod"}},
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


def test_hosts_apply_creates_host_via_nested_endpoint(fake_aap: Any, tmp_path: Path) -> None:
    """Apply a Host doc — strategy POSTs to ``/inventories/<id>/hosts/``."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
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
    hosts = list(fake_aap.store["hosts"].values())
    assert len(hosts) == 1
    assert hosts[0]["name"] == "web-01"
    assert hosts[0]["inventory"] == 20
    assert hosts[0]["description"] == "Frontend web server"


def test_hosts_apply_preview_does_not_write(fake_aap: Any, tmp_path: Path) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
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
    assert fake_aap.store["hosts"] == {}


def test_hosts_save_round_trips_to_yaml(fake_aap: Any) -> None:
    _seed_inventory_with_hosts(fake_aap)
    result = CliRunner().invoke(app, ["hosts", "save", "web-01"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    # Save dumps YAML — exact field ordering varies, but kind + name must appear.
    assert "kind: Host" in out
    assert "web-01" in out
