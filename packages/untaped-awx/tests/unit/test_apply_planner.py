"""Unit tests for ApplyPlanner and scope_for.

ApplyPlanner builds the identity dict and the resolved payload dict the
apply pipeline hands to its strategy. scope_for is the FK-lookup-scope
helper used by both the planner (for FK resolution) and apply_file's
prefetch (so warm-up reads the same buckets as the apply path).
"""

from __future__ import annotations

from typing import Any, cast

from untaped_awx.application.apply_planner import ApplyPlanner, scope_for
from untaped_awx.application.ports import FkResolver
from untaped_awx.domain import FkRef, Metadata, Resource
from untaped_awx.domain.envelope import IdentityRef
from untaped_awx.infrastructure.specs import (
    GROUP_SPEC,
    JOB_TEMPLATE_SPEC,
    PROJECT_SPEC,
    SCHEDULE_SPEC,
)


class _StubFk:
    def __init__(self, names: dict[tuple[str, str], int]) -> None:
        self._names = names

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        return self._names[(kind, name)]

    def id_to_name(self, kind: str, id_: int) -> str:
        raise NotImplementedError

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        raise NotImplementedError

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        return None


# ---- plan_identity ----


def test_plan_identity_org_scoped_kind() -> None:
    """JobTemplate identity is ``(name, organization)``."""
    planner = ApplyPlanner()
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={},
    )
    assert planner.plan_identity(JOB_TEMPLATE_SPEC, resource) == {
        "name": "deploy",
        "organization": "Default",
    }


def test_plan_identity_omits_organization_for_kinds_without_it() -> None:
    """Group's identity is ``(name,)`` (parent comes via metadata.parent);
    organization isn't in identity_keys so it's not in the dict."""
    planner = ApplyPlanner()
    resource = Resource(
        kind="Group",
        metadata=Metadata(
            name="g1",
            parent=IdentityRef(kind="Inventory", name="prod", organization="Default"),
        ),
        spec={},
    )
    identity = planner.plan_identity(GROUP_SPEC, resource)
    assert "organization" not in identity
    assert identity["name"] == "g1"
    assert identity["parent"] == resource.metadata.parent


def test_plan_identity_includes_parent_when_present() -> None:
    """Schedule has a polymorphic parent — identity must carry it."""
    planner = ApplyPlanner()
    resource = Resource(
        kind="Schedule",
        metadata=Metadata(
            name="nightly",
            parent=IdentityRef(kind="JobTemplate", name="deploy", organization="Default"),
        ),
        spec={"rrule": "FREQ=DAILY"},
    )
    identity = planner.plan_identity(SCHEDULE_SPEC, resource)
    assert identity["parent"] == resource.metadata.parent


# ---- plan_payload ----


def test_plan_payload_projects_canonical_fields() -> None:
    planner = ApplyPlanner()
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"scm_type": "git", "scm_url": "https://example.com", "ignored": "junk"},
    )
    payload = planner.plan_payload(
        PROJECT_SPEC,
        resource,
        fk=cast(FkResolver, _StubFk({("Organization", "Default"): 1})),
    )
    assert payload["scm_type"] == "git"
    assert payload["scm_url"] == "https://example.com"
    assert "ignored" not in payload  # not in canonical_fields


def test_plan_payload_injects_identity_keys_from_metadata() -> None:
    """Even when the user omits ``name`` from spec body, the create
    payload must include it (and ``organization`` for org-scoped kinds)
    so AWX can persist the row."""
    planner = ApplyPlanner()
    resource = Resource(
        kind="Project",
        metadata=Metadata(name="playbooks", organization="Default"),
        spec={"scm_type": "git"},
    )
    payload = planner.plan_payload(
        PROJECT_SPEC,
        resource,
        fk=cast(FkResolver, _StubFk({("Organization", "Default"): 1})),
    )
    assert payload["name"] == "playbooks"
    assert payload["organization"] == 1  # FK-resolved to id


def test_plan_payload_resolves_single_fks_to_ids() -> None:
    planner = ApplyPlanner()
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={
            "playbook": "deploy.yml",
            "project": "playbooks",
            "inventory": "prod",
        },
    )
    payload = planner.plan_payload(
        JOB_TEMPLATE_SPEC,
        resource,
        fk=cast(
            FkResolver,
            _StubFk(
                {
                    ("Organization", "Default"): 1,
                    ("Project", "playbooks"): 5,
                    ("Inventory", "prod"): 7,
                }
            ),
        ),
    )
    assert payload["project"] == 5
    assert payload["inventory"] == 7


def test_plan_payload_resolves_multi_fks_to_id_lists() -> None:
    planner = ApplyPlanner()
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={
            "playbook": "deploy.yml",
            "credentials": ["ssh-key", "vault-pw"],
        },
    )
    payload = planner.plan_payload(
        JOB_TEMPLATE_SPEC,
        resource,
        fk=cast(
            FkResolver,
            _StubFk(
                {
                    ("Organization", "Default"): 1,
                    ("Credential", "ssh-key"): 10,
                    ("Credential", "vault-pw"): 11,
                }
            ),
        ),
    )
    assert payload["credentials"] == [10, 11]


def test_plan_payload_drops_sub_endpoint_multi_fk_from_body() -> None:
    """``Group.hosts`` is reconciled via associate/disassociate POSTs, not
    via PATCH body. The planner must strip it from the payload."""
    planner = ApplyPlanner()
    resource = Resource(
        kind="Group",
        metadata=Metadata(
            name="g1",
            parent=IdentityRef(kind="Inventory", name="prod", organization="Default"),
        ),
        spec={"description": "d", "hosts": ["web-01", "web-02"]},
    )
    payload = planner.plan_payload(
        GROUP_SPEC,
        resource,
        fk=cast(FkResolver, _StubFk({})),
    )
    assert "hosts" not in payload
    assert "children" not in payload
    assert payload["description"] == "d"


def test_plan_payload_skips_polymorphic_fks() -> None:
    """Schedule's ``unified_job_template`` is a polymorphic FK that lives
    on metadata.parent, not in the body. The planner must not try to
    resolve it via spec.fk_refs."""
    planner = ApplyPlanner()
    resource = Resource(
        kind="Schedule",
        metadata=Metadata(
            name="nightly",
            parent=IdentityRef(kind="JobTemplate", name="deploy", organization="Default"),
        ),
        spec={"rrule": "FREQ=DAILY", "enabled": True},
    )
    payload = planner.plan_payload(
        SCHEDULE_SPEC,
        resource,
        fk=cast(FkResolver, _StubFk({})),
    )
    assert payload["rrule"] == "FREQ=DAILY"
    assert payload["enabled"] is True


# ---- scope_for ----


def test_scope_for_organization_scope() -> None:
    """Org-scoped FK refs (e.g. JobTemplate.project) resolve names within
    the resource's own organization."""
    ref = FkRef(field="project", kind="Project", scope_field="organization")
    resource = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="t", organization="Default"),
        spec={},
    )
    assert scope_for(ref, resource) == {"organization": "Default"}


def test_scope_for_organization_prefers_parent_org_when_present() -> None:
    """Schedule's polymorphic parent carries the canonical org for
    name-scoped FK lookups."""
    ref = FkRef(field="x", kind="Y", scope_field="organization")
    resource = Resource(
        kind="Schedule",
        metadata=Metadata(
            name="nightly",
            parent=IdentityRef(kind="JobTemplate", name="deploy", organization="ParentOrg"),
            organization="OwnOrg",
        ),
        spec={},
    )
    assert scope_for(ref, resource) == {"organization": "ParentOrg"}


def test_scope_for_inventory_scope() -> None:
    """Inventory-child kinds (Host, Group) scope by inventory, not org."""
    ref = GROUP_SPEC.fk_refs[0]  # hosts, scope_field="inventory"
    resource = Resource(
        kind="Group",
        metadata=Metadata(
            name="g1",
            parent=IdentityRef(kind="Inventory", name="prod", organization="Default"),
        ),
        spec={},
    )
    assert scope_for(ref, resource) == {
        "inventory": "prod",
        "inventory__organization": "Default",
    }


def test_scope_for_returns_none_when_no_scope_field() -> None:
    ref = FkRef(field="x", kind="Y")  # no scope_field
    resource = Resource(
        kind="Anything",
        metadata=Metadata(name="n"),
        spec={},
    )
    assert scope_for(ref, resource) is None
