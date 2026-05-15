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
        "groups",
        id=201,
        name="api-servers",
        inventory=20,
        inventory_name="prod",
        description="api tier",
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


def test_groups_apply_rejects_non_list_hosts(
    fake_aap: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare string under ``hosts:`` would otherwise be normalised to ``[]``
    and silently disassociate every existing member on ``--yes`` — the worst
    possible failure mode. Reject at the boundary instead."""
    monkeypatch.setenv("COLUMNS", "200")
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
          hosts: web-01
        """
    )
    result = CliRunner().invoke(app, ["groups", "apply", "--file", str(doc), "--yes"])
    assert result.exit_code != 0
    assert "must be a list" in result.output
    # Critical: existing membership must NOT have been wiped.
    assert fake_aap.memberships[("groups", 200, "hosts")] == {101}


def test_groups_save_emits_metadata_parent_and_membership(fake_aap: Any) -> None:
    """Critical for round-trip: a saved Group must include
    ``metadata.parent`` (Inventory) AND its ``hosts``/``children`` lists
    so applying it back through ``InventoryChildApplyStrategy`` succeeds
    AND restores membership (otherwise re-apply silently disassociates
    every member because absent + empty list both look like ``[]`` in
    ``MembershipReconciler.plan`` — wait, absent is unmanaged; but a save
    that omits the list isn't a fix, it's just a different breakage)."""
    import yaml as _yaml

    _seed_groups(fake_aap)
    # Seed two hosts and pre-populate Group 200's membership.
    fake_aap.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    fake_aap.seed("hosts", id=102, name="web-02", inventory=20, inventory_name="prod")
    fake_aap.memberships[("groups", 200, "hosts")] = {101, 102}
    # And one child group.
    fake_aap.memberships[("groups", 200, "children")] = {201}

    result = CliRunner().invoke(app, ["groups", "save", "web-servers"])
    assert result.exit_code == 0, result.output
    parsed = _yaml.safe_load(result.stdout)
    assert parsed["kind"] == "Group"
    assert parsed["metadata"]["parent"]["kind"] == "Inventory"
    assert parsed["metadata"]["parent"]["name"] == "prod"
    assert parsed["metadata"]["parent"]["organization"] == "Default"
    assert sorted(parsed["spec"]["hosts"]) == ["web-01", "web-02"]
    assert parsed["spec"]["children"] == ["api-servers"]


def test_groups_save_round_trips_through_apply(fake_aap: Any, tmp_path: Path) -> None:
    """End-to-end: save a Group with membership, apply it back, expect
    ``unchanged`` (the host/group ids resolve to the same set)."""
    _seed_groups(fake_aap)
    fake_aap.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    fake_aap.memberships[("groups", 200, "hosts")] = {101}
    fake_aap.memberships[("groups", 200, "children")] = {201}

    save_result = CliRunner().invoke(app, ["groups", "save", "web-servers"])
    assert save_result.exit_code == 0, save_result.output
    saved = tmp_path / "group.yml"
    saved.write_text(save_result.stdout)
    apply_result = CliRunner().invoke(app, ["groups", "apply", "--file", str(saved), "--yes"])
    assert apply_result.exit_code == 0, apply_result.output
    assert "unchanged" in apply_result.output
    # Membership preserved exactly.
    assert fake_aap.memberships[("groups", 200, "hosts")] == {101}
    assert fake_aap.memberships[("groups", 200, "children")] == {201}


def test_groups_apply_disambiguates_inventory_by_parent_organization(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """When two organizations have an inventory with the same name, the
    Group's ``metadata.parent.organization`` must scope the FK lookup so
    AWX returns the host belonging to the right org's inventory."""
    seeded_default_org.seed("organizations", id=2, name="Other")
    # Two inventories named "prod" — one per organization.
    seeded_default_org.seed(
        "inventories", id=20, name="prod", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "inventories", id=21, name="prod", organization=2, organization_name="Other"
    )
    # web-01 in Default's prod, web-01 also exists in Other's prod.
    seeded_default_org.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    seeded_default_org.seed("hosts", id=102, name="web-01", inventory=21, inventory_name="prod")

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
          description: Web tier in Default
          hosts:
            - web-01
        """
    )
    result = CliRunner().invoke(app, ["groups", "apply", "--file", str(doc), "--yes"])
    assert result.exit_code == 0, result.output
    new_group = next(g for g in seeded_default_org.store["groups"].values() if g["inventory"] == 20)
    # Critical: the host associated must be Default's web-01 (id=101), not Other's (id=102).
    assert seeded_default_org.memberships[("groups", new_group["id"], "hosts")] == {101}


def test_apply_file_resolves_sibling_group_children(fake_aap: Any, tmp_path: Path) -> None:
    """Regression for two-phase apply: a Group whose ``children:`` references
    a sibling Group declared later in the same file would fail with
    ``Group not found`` when phase 1 tried to resolve the child during
    membership planning. Phase 2 now reconciles memberships *after* every
    body has been written, so cycles within one inventory file work.
    """
    _seed_inventory(fake_aap)
    fake_aap.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    fake_aap.seed("hosts", id=102, name="api-01", inventory=20, inventory_name="prod")

    doc = tmp_path / "inventory.yml"
    # ``app-tier`` references ``web-servers`` and ``api-servers`` as
    # children. Topological sort within Group is alphabetical
    # (api-servers, app-tier, web-servers) so app-tier applies before
    # web-servers — exactly the case the previous single-pass apply
    # failed on.
    doc.write_text(
        "kind: Group\n"
        "metadata:\n"
        "  name: app-tier\n"
        "  parent: { kind: Inventory, name: prod, organization: Default }\n"
        "spec:\n"
        "  description: Roll-up\n"
        "  children:\n"
        "    - web-servers\n"
        "    - api-servers\n"
        "---\n"
        "kind: Group\n"
        "metadata:\n"
        "  name: web-servers\n"
        "  parent: { kind: Inventory, name: prod, organization: Default }\n"
        "spec:\n"
        "  description: Web tier\n"
        "  hosts: [web-01]\n"
        "---\n"
        "kind: Group\n"
        "metadata:\n"
        "  name: api-servers\n"
        "  parent: { kind: Inventory, name: prod, organization: Default }\n"
        "spec:\n"
        "  description: API tier\n"
        "  hosts: [api-01]\n"
    )
    result = CliRunner().invoke(app, ["apply", "--file", str(doc), "--yes"])
    assert result.exit_code == 0, result.output
    # Every Group created.
    groups_by_name = {g["name"]: g for g in fake_aap.store["groups"].values()}
    assert set(groups_by_name) == {"app-tier", "web-servers", "api-servers"}
    # Memberships reconciled in phase 2.
    web_id = groups_by_name["web-servers"]["id"]
    api_id = groups_by_name["api-servers"]["id"]
    app_id = groups_by_name["app-tier"]["id"]
    assert fake_aap.memberships[("groups", app_id, "children")] == {web_id, api_id}
    assert fake_aap.memberships[("groups", web_id, "hosts")] == {101}
    assert fake_aap.memberships[("groups", api_id, "hosts")] == {102}


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


# ---- `groups <sub_endpoint> add/remove` (spec-driven membership commands) ----


def _seed_two_hosts(fake: Any) -> None:
    fake.seed(
        "hosts",
        id=101,
        name="web-01",
        inventory=20,
        inventory_name="prod",
        summary_fields={"inventory": {"id": 20, "name": "prod"}},
    )
    fake.seed(
        "hosts",
        id=102,
        name="web-02",
        inventory=20,
        inventory_name="prod",
        summary_fields={"inventory": {"id": 20, "name": "prod"}},
    )


def test_groups_hosts_add_associates_via_stdin(fake_aap: Any) -> None:
    """`hosts list … | groups hosts add <group> --stdin` POSTs each
    host's id into the group's sub-endpoint."""
    _seed_groups(fake_aap)
    _seed_two_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        ["groups", "hosts", "add", "web-servers", "--stdin"],
        input="web-01\nweb-02\n",
    )
    assert result.exit_code == 0, result.output
    assert fake_aap.memberships[("groups", 200, "hosts")] == {101, 102}


def test_groups_hosts_add_accepts_positional_names(fake_aap: Any) -> None:
    _seed_groups(fake_aap)
    _seed_two_hosts(fake_aap)
    result = CliRunner().invoke(app, ["groups", "hosts", "add", "web-servers", "web-01", "web-02"])
    assert result.exit_code == 0, result.output
    assert fake_aap.memberships[("groups", 200, "hosts")] == {101, 102}


def test_groups_hosts_add_rejects_mixed_positional_and_stdin(fake_aap: Any) -> None:
    _seed_groups(fake_aap)
    _seed_two_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        ["groups", "hosts", "add", "web-servers", "web-01", "--stdin"],
        input="web-02\n",
    )
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "stdin" in output.lower()
    # Neither host was associated — the command rejected up front.
    assert fake_aap.memberships[("groups", 200, "hosts")] == set()


def test_groups_hosts_add_empty_stdin_errors(fake_aap: Any) -> None:
    _seed_groups(fake_aap)
    result = CliRunner().invoke(app, ["groups", "hosts", "add", "web-servers", "--stdin"], input="")
    assert result.exit_code != 0
    assert "no identifiers received on stdin" in (result.output + (result.stderr or ""))


def test_groups_hosts_remove_disassociates_listed_members(fake_aap: Any) -> None:
    """`remove` sends ``{id, disassociate: true}``; only listed members
    drop, the rest stay."""
    _seed_groups(fake_aap)
    _seed_two_hosts(fake_aap)
    fake_aap.memberships[("groups", 200, "hosts")] = {101, 102}
    result = CliRunner().invoke(app, ["groups", "hosts", "remove", "web-servers", "web-01"])
    assert result.exit_code == 0, result.output
    assert fake_aap.memberships[("groups", 200, "hosts")] == {102}


def test_groups_hosts_add_continues_on_missing_name(fake_aap: Any) -> None:
    """A missing member name surfaces per-id on stderr and exits 1, but
    the names that resolved still get associated."""
    _seed_groups(fake_aap)
    _seed_two_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        ["groups", "hosts", "add", "web-servers", "--stdin"],
        input="web-01\nghost\n",
    )
    assert result.exit_code != 0
    assert fake_aap.memberships[("groups", 200, "hosts")] == {101}
    assert "ghost" in (result.output + (result.stderr or ""))


def test_groups_hosts_add_disambiguates_parent_with_inventory_flag(fake_aap: Any) -> None:
    """When the same group name lives in two inventories, ``--inventory``
    picks the right parent for the add. Without it, a name lookup is
    global and the test for "first match wins" is brittle — so the
    flag must round-trip through ``scope_for_spec`` into the lookup."""
    _seed_inventory(fake_aap)
    fake_aap.seed(
        "inventories",
        id=21,
        name="staging",
        organization=1,
        organization_name="Default",
        kind="",
    )
    fake_aap.seed(
        "groups",
        id=200,
        name="web-servers",
        inventory=20,
        inventory_name="prod",
        summary_fields={"inventory": {"id": 20, "name": "prod"}},
    )
    fake_aap.seed(
        "groups",
        id=300,
        name="web-servers",
        inventory=21,
        inventory_name="staging",
        summary_fields={"inventory": {"id": 21, "name": "staging"}},
    )
    fake_aap.seed(
        "hosts",
        id=101,
        name="web-01",
        inventory=21,
        inventory_name="staging",
        summary_fields={"inventory": {"id": 21, "name": "staging"}},
    )
    result = CliRunner().invoke(
        app,
        ["groups", "hosts", "add", "web-servers", "web-01", "--inventory", "staging"],
    )
    assert result.exit_code == 0, result.output
    # The staging group (id=300) got the association — not the prod one (200).
    assert fake_aap.memberships[("groups", 300, "hosts")] == {101}
    assert fake_aap.memberships[("groups", 200, "hosts")] == set()


def test_groups_hosts_add_resolves_by_id_skipping_scope(fake_aap: Any) -> None:
    """Numeric ids bypass name lookup so the scope (inventory) doesn't
    matter — the natural pipeline shape when piping FK columns."""
    _seed_groups(fake_aap)
    _seed_two_hosts(fake_aap)
    result = CliRunner().invoke(
        app,
        ["groups", "hosts", "add", "web-servers", "--stdin"],
        input="101\n102\n",
    )
    assert result.exit_code == 0, result.output
    assert fake_aap.memberships[("groups", 200, "hosts")] == {101, 102}


def test_groups_children_add_associates(fake_aap: Any) -> None:
    """Spec-driven coverage: ``Group.children`` is also
    ``FkRef(multi=True, sub_endpoint="children")`` so the same factory
    wires ``groups children add`` with identical option surface."""
    _seed_groups(fake_aap)  # seeds web-servers (200) and api-servers (201)
    result = CliRunner().invoke(
        app,
        ["groups", "children", "add", "web-servers", "api-servers"],
    )
    assert result.exit_code == 0, result.output
    assert fake_aap.memberships[("groups", 200, "children")] == {201}
