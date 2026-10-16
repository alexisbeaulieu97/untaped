"""The documents loader: what each document becomes, and what each reference resolves to."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pytest

from untaped_awx.api import AwxResources, Linted
from untaped_awx.application.lint.documents import load_documents
from untaped_awx.domain import IdentityRef
from untaped_awx.errors import PermissionDeniedError, ResourceNotFoundError
from untaped_awx.infrastructure.lint.documents import NOT_TEXT, documents_of
from untaped_awx.infrastructure.resources.catalog import AwxResourceCatalog
from untaped_awx.testing import resources
from untaped_lint.api import DocumentLocation

_PROJECT = "kind: Project\nmetadata: {name: web, organization: Platform}\nspec: {}\n"


def _template(organization: str = "Platform", **spec: str) -> str:
    body = ", ".join(f"{key}: {value}" for key, value in {"project": "web", **spec}.items())
    return f"kind: JobTemplate\nmetadata: {{name: deploy, organization: {organization}}}\n" + (
        f"spec: {{{body}}}\n"
    )


def _item(snapshot: AwxResources, kind: str, name: str) -> Linted:
    return next(each for each in snapshot.of(kind) if each.resource.metadata.name == name)


def _states(
    snapshot: AwxResources, kind: str = "JobTemplate", name: str = "deploy"
) -> dict[str, str]:
    return {ref.field: ref.resolution for ref in _item(snapshot, kind, name).references}


# --- reference states, offline ----------------------------------------------


def test_a_reference_the_batch_holds_resolves() -> None:
    assert _states(resources(_template(), _PROJECT)) == {"project": "resolved"}


def test_a_name_missing_from_a_kind_the_batch_holds_is_absent() -> None:
    other = _PROJECT.replace("name: web", "name: api")
    assert _states(resources(_template(), other)) == {"project": "absent"}


def test_a_kind_the_batch_does_not_hold_is_unresolved() -> None:
    snapshot = resources(_template())
    assert _states(snapshot) == {"project": "unresolved"}
    assert snapshot.loaded == ("JobTemplate",)
    assert snapshot.online is False


def test_a_reference_resolves_in_the_template_organization_only() -> None:
    elsewhere = _PROJECT.replace("Platform", "Finance")
    assert _states(resources(_template(), elsewhere)) == {"project": "absent"}


def test_an_ambiguous_reference_counts_as_resolved() -> None:
    org_less = "kind: JobTemplate\nmetadata: {name: deploy}\nspec: {project: web}\n"
    both = _PROJECT + "---\n" + _PROJECT.replace("Platform", "Finance")
    assert _states(resources(org_less, both)) == {"project": "resolved"}


def test_each_member_of_a_multi_reference_is_its_own_reference() -> None:
    credential = "kind: Credential\nmetadata: {name: ssh, organization: Platform}\nspec: {}\n"
    snapshot = resources(_template(credentials="[ssh, vault]"), _PROJECT, credential)
    references = _item(snapshot, "JobTemplate", "deploy").references
    assert [(ref.field, ref.target.name, ref.resolution) for ref in references] == [
        ("project", "web", "resolved"),
        ("credentials", "ssh", "resolved"),
        ("credentials", "vault", "absent"),
    ]
    assert references[1].target == IdentityRef(
        kind="Credential", name="ssh", organization="Platform"
    )


def test_required_references_written_as_null_are_absent_and_unnamed() -> None:
    snapshot = resources(
        _template(project="null", inventory="null", ask_inventory_on_launch="false")
    )
    references = _item(snapshot, "JobTemplate", "deploy").references
    assert [
        (ref.field, ref.target.kind, ref.target.name, ref.resolution) for ref in references
    ] == [
        ("project", "Project", "", "absent"),
        ("inventory", "Inventory", "", "absent"),
    ]


def test_a_schedule_parent_and_a_workflow_node_resolve_as_apply_resolves_them() -> None:
    schedule = (
        "kind: Schedule\nmetadata:\n  name: nightly\n"
        "  parent: {kind: JobTemplate, name: deploy, organization: Platform}\nspec: {}\n"
    )
    workflow = (
        "kind: WorkflowJobTemplate\nmetadata: {name: release, organization: Platform}\n"
        "spec:\n  nodes:\n    - {id: one, run: {job_template: deploy}}\n"
        "    - {id: two, run: {job_template: missing}}\n"
    )
    snapshot = resources(_template(), _PROJECT, schedule, workflow)
    assert _states(snapshot, "Schedule", "nightly") == {"parent": "resolved"}
    assert _states(snapshot, "WorkflowJobTemplate", "release") == {
        "nodes[one].run": "resolved",
        "nodes[two].run": "absent",
    }


def test_an_inventory_child_resolves_through_its_inventory() -> None:
    inventory = "kind: Inventory\nmetadata: {name: prod, organization: Platform}\nspec: {}\n"
    host = (
        "kind: Host\nmetadata:\n  name: web1\n"
        "  parent: {kind: Inventory, name: prod, organization: Platform}\n"
    )
    group = (
        "kind: Group\nmetadata:\n  name: web\n"
        "  parent: {kind: Inventory, name: prod, organization: Platform}\n"
        "spec: {hosts: [web1, web2]}\n"
    )
    snapshot = resources(inventory, host, group)
    refs = _item(snapshot, "Group", "web").references
    assert [(ref.target.name, ref.resolution) for ref in refs] == [
        ("web1", "resolved"),
        ("web2", "absent"),
    ]
    assert refs[0].target.parent == IdentityRef(
        kind="Inventory", name="prod", organization="Platform"
    )


# --- documents ---------------------------------------------------------------


def test_index_counts_every_document_and_unreadable_ones_are_carried() -> None:
    text = "\n---\n".join(
        [_PROJECT, "", "kind: Nope\nmetadata: {name: x}", "[1]", "key: [", _PROJECT]
    )
    snapshot = resources(text)
    assert [each.location for each in snapshot.items] == [
        DocumentLocation(path="text1.yml", index=1)
    ]  # the empty second document counts; nothing after the YAML error is read
    assert [(each.index, each.error) for each in snapshot.unreadable] == [
        (3, snapshot.unreadable[0].error),
        (4, "each YAML doc must be a mapping"),
        (5, snapshot.unreadable[2].error),
    ]
    assert snapshot.unreadable[0].error.startswith("unknown kind 'Nope'")
    assert snapshot.unreadable[2].error.startswith("invalid YAML: ")


def test_an_undecodable_file_is_one_unreadable_document() -> None:
    snapshot = load_documents(documents_of([("bad.yml", None)]), catalog=AwxResourceCatalog())
    assert [(each.path, each.index, each.error) for each in snapshot.unreadable] == [
        ("bad.yml", 1, NOT_TEXT)
    ]


def test_testing_resources_is_the_offline_loader() -> None:
    loaded = load_documents(
        documents_of([("text1.yml", _template()), ("text2.yml", _PROJECT)]),
        catalog=AwxResourceCatalog(),
    )
    assert resources(_template(), _PROJECT) == loaded


def test_every_catalog_kind_has_its_facts() -> None:
    snapshot = resources(_PROJECT)
    assert set(snapshot.kinds) == set(AwxResourceCatalog().kinds())
    assert "scm_url" in snapshot.kinds["Project"].known_fields


# --- online --------------------------------------------------------------------


class _Controller:
    """A live resolver knowing one ``Inventory`` named ``prod``; Credentials are forbidden."""

    def __init__(self) -> None:
        self.prefetched: list[dict[str, list[dict[str, str] | None]]] = []

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        if kind == "Credential":
            raise PermissionDeniedError("forbidden")
        if (kind, name) == ("Inventory", "prod"):
            return 7
        raise ResourceNotFoundError(kind, {"name": name, **(scope or {})})

    def validate_id(self, kind: str, id_: int, *, scope: dict[str, str] | None = None) -> int:
        return id_

    def id_to_identity(self, kind: str, id_: int) -> IdentityRef:
        raise NotImplementedError

    def id_to_name(self, kind: str, id_: int) -> str:
        raise NotImplementedError

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        raise NotImplementedError

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        self.prefetched.append(plan)

    def seed(self, kind: str, records: Iterable[Mapping[str, Any]]) -> None:
        raise NotImplementedError


def test_online_the_controller_answers_what_the_batch_lacks() -> None:
    live = _Controller()
    text = _template(inventory="prod", execution_environment="ee", credentials="[vault]")
    snapshot = load_documents(
        documents_of([("t.yml", text), ("p.yml", _PROJECT)]),
        catalog=AwxResourceCatalog(),
        live=live,
    )
    assert _states(snapshot) == {
        "project": "resolved",
        "inventory": "resolved",
        "execution_environment": "absent",
        "credentials": "unresolved",
    }
    assert snapshot.online is True
    kinds = AwxResourceCatalog().kinds()
    assert snapshot.loaded == tuple(kind for kind in kinds if kind != "Credential")  # 403
    assert live.prefetched and "Inventory" in live.prefetched[0]


@pytest.mark.parametrize("value", ['"14"', "{name: web, organization: Finance}"])
def test_a_quoted_number_is_a_name_and_a_written_scope_is_searched(value: str) -> None:
    snapshot = resources(_template(project=value), _PROJECT)
    assert _states(snapshot) == {"project": "absent"}


def test_an_id_cannot_be_checked_offline() -> None:
    assert _states(resources(_template(project="14"), _PROJECT)) == {"project": "unresolved"}


# --- find --------------------------------------------------------------------------


def test_find_reads_the_items_a_copy_holds_not_the_originals() -> None:
    """N2: the name index belongs to the items it was built from."""
    snapshot = resources(_PROJECT)
    target = IdentityRef(kind="Project", name="web", organization="Platform")
    assert snapshot.find(target) is not None
    assert snapshot.model_copy(update={"items": ()}).find(target) is None
    other = resources(_PROJECT.replace("name: web", "name: api"))
    assert snapshot.model_copy(update={"items": other.items}).find(target) is None


def test_find_names_no_item_when_several_answer() -> None:
    """N8: an org-less reference to a name two organizations hold judges neither."""
    both = resources(_PROJECT, _PROJECT.replace("Platform", "Finance"))
    assert both.find(IdentityRef(kind="Project", name="web")) is None
    assert both.find(IdentityRef(kind="Project", name="web", organization="Finance")) is not None
