"""End-to-end CLI tests for ``untaped awx groups`` against ``FakeAap``,
including the apply path's sub-endpoint membership reconciliation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from untaped_awx import app

pytestmark = pytest.mark.integration


def _seed_inventory(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )


def _seed_groups(fake: Any) -> None:
    _seed_inventory(fake)
    fake.seed(
        "groups",
        id=200,
        name="web-servers",
        inventory=20,
        inventory_name="prod",
        description="web tier",
        variables="",
        summary_fields={"inventory": {"id": 20, "name": "prod"}},
    )
    fake.seed(
        "groups",
        id=201,
        name="api-servers",
        inventory=20,
        inventory_name="prod",
        description="api tier",
        variables="",
        summary_fields={"inventory": {"id": 20, "name": "prod"}},
    )


def test_groups_list_returns_seeded_records(fake_aap: Any) -> None:
    _seed_groups(fake_aap)
    result = CliRunner().invoke(app, ["groups", "list", "--format", "raw", "--columns", "name"])
    assert result.exit_code == 0, result.output
    names = sorted(result.stdout.strip().splitlines())
    assert names == ["api-servers", "web-servers"]


def test_groups_get_by_id(fake_aap: Any) -> None:
    _seed_groups(fake_aap)
    result = CliRunner().invoke(
        app, ["groups", "get", "200", "--format", "raw", "--columns", "name"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "web-servers"


def test_groups_apply_creates_group_and_associates_hosts(fake_aap: Any, tmp_path: Path) -> None:
    """Apply a Group with ``hosts:`` reconciles membership via
    ``POST /groups/<id>/hosts/`` per host id."""
    _seed_inventory(fake_aap)
    # Pre-seed the hosts so name → id lookups succeed.
    fake_aap.seed(
        "hosts",
        id=101,
        name="web-01",
        inventory=20,
        inventory_name="prod",
    )
    fake_aap.seed(
        "hosts",
        id=102,
        name="web-02",
        inventory=20,
        inventory_name="prod",
    )
    doc = tmp_path / "group.yml"
    doc.write_text(
        """
        kind: Group
        metadata:
          name: web-servers
          parent:
            kind: Inventory
            name: prod
            organization: Default
        spec:
          description: Web tier
          hosts:
            - web-01
            - web-02
        """
    )
    result = CliRunner().invoke(app, ["groups", "apply", "--file", str(doc), "--yes"])
    assert result.exit_code == 0, result.output
    # Group record exists under inventory 20.
    groups = list(fake_aap.store["groups"].values())
    assert len(groups) == 1
    new_group = groups[0]
    assert new_group["name"] == "web-servers"
    assert new_group["inventory"] == 20
    # Membership was reconciled: both hosts associated.
    members = fake_aap.memberships[("groups", new_group["id"], "hosts")]
    assert members == {101, 102}


def test_groups_apply_disassociates_removed_hosts(fake_aap: Any, tmp_path: Path) -> None:
    """Re-apply with one host removed → disassociate POST."""
    _seed_inventory(fake_aap)
    fake_aap.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    fake_aap.seed("hosts", id=102, name="web-02", inventory=20, inventory_name="prod")
    fake_aap.seed(
        "groups",
        id=200,
        name="web-servers",
        inventory=20,
        inventory_name="prod",
        description="Web tier",
    )
    # Pre-populate membership: both hosts already in the group.
    fake_aap.memberships[("groups", 200, "hosts")] = {101, 102}

    doc = tmp_path / "group.yml"
    doc.write_text(
        """
        kind: Group
        metadata:
          name: web-servers
          parent:
            kind: Inventory
            name: prod
            organization: Default
        spec:
          description: Web tier
          hosts:
            - web-01
        """
    )
    result = CliRunner().invoke(app, ["groups", "apply", "--file", str(doc), "--yes"])
    assert result.exit_code == 0, result.output
    # web-02 was disassociated; web-01 remains.
    assert fake_aap.memberships[("groups", 200, "hosts")] == {101}


def test_groups_apply_preview_shows_membership_diff_without_writes(
    fake_aap: Any, tmp_path: Path
) -> None:
    _seed_inventory(fake_aap)
    fake_aap.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    fake_aap.seed(
        "groups",
        id=200,
        name="web-servers",
        inventory=20,
        inventory_name="prod",
        description="Web tier",
    )
    fake_aap.memberships[("groups", 200, "hosts")] = set()  # currently empty

    doc = tmp_path / "group.yml"
    doc.write_text(
        """
        kind: Group
        metadata:
          name: web-servers
          parent:
            kind: Inventory
            name: prod
            organization: Default
        spec:
          description: Web tier
          hosts:
            - web-01
        """
    )
    result = CliRunner().invoke(app, ["groups", "apply", "--file", str(doc)])
    assert result.exit_code == 0, result.output
    # No writes — membership stays empty.
    assert fake_aap.memberships[("groups", 200, "hosts")] == set()
    # Preview output mentions the host membership change.
    assert "hosts" in result.output
    assert "web-01" in result.output


def test_groups_apply_associates_child_groups(fake_aap: Any, tmp_path: Path) -> None:
    """``children:`` reconciles via ``POST /groups/<id>/children/``."""
    _seed_inventory(fake_aap)
    fake_aap.seed(
        "groups",
        id=201,
        name="api-servers",
        inventory=20,
        inventory_name="prod",
        description="API",
    )
    doc = tmp_path / "group.yml"
    doc.write_text(
        """
        kind: Group
        metadata:
          name: web-servers
          parent:
            kind: Inventory
            name: prod
            organization: Default
        spec:
          description: Web tier
          children:
            - api-servers
        """
    )
    result = CliRunner().invoke(app, ["groups", "apply", "--file", str(doc), "--yes"])
    assert result.exit_code == 0, result.output
    # The new group was created; api-servers was associated as a child.
    new_group = next(g for g in fake_aap.store["groups"].values() if g["name"] == "web-servers")
    assert fake_aap.memberships[("groups", new_group["id"], "children")] == {201}


def test_groups_apply_unchanged_when_membership_matches(fake_aap: Any, tmp_path: Path) -> None:
    _seed_inventory(fake_aap)
    fake_aap.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    fake_aap.seed(
        "groups",
        id=200,
        name="web-servers",
        inventory=20,
        inventory_name="prod",
        description="Web tier",
    )
    fake_aap.memberships[("groups", 200, "hosts")] = {101}

    doc = tmp_path / "group.yml"
    doc.write_text(
        """
        kind: Group
        metadata:
          name: web-servers
          parent:
            kind: Inventory
            name: prod
            organization: Default
        spec:
          description: Web tier
          hosts:
            - web-01
        """
    )
    result = CliRunner().invoke(app, ["groups", "apply", "--file", str(doc), "--yes"])
    assert result.exit_code == 0, result.output
    # Membership preserved exactly.
    assert fake_aap.memberships[("groups", 200, "hosts")] == {101}
