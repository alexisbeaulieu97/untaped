"""Inventory lifecycle contracts with controller-shaped structured text."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from untaped.capabilities.awx.application.apply_ordering import topological_sort
from untaped.capabilities.awx.application.apply_verifier import ApplyVerifier
from untaped.capabilities.awx.application.delete_resource import DeleteResource
from untaped.capabilities.awx.application.mutation_engine import (
    BatchMutationEngine,
    MutationConflict,
)
from untaped.capabilities.awx.application.mutation_values import semantic_equal
from untaped.capabilities.awx.application.save_resource import SaveResource
from untaped.capabilities.awx.application.save_resources import resource_filename
from untaped.capabilities.awx.domain import IdentityRef, Metadata, Resource
from untaped.capabilities.awx.errors import BadRequest
from untaped.capabilities.awx.infrastructure import AwxClient, AwxConfig
from untaped.capabilities.awx.infrastructure.catalog import AwxResourceCatalog
from untaped.capabilities.awx.infrastructure.fk_resolver import FkResolver
from untaped.capabilities.awx.infrastructure.resource_repo import ResourceRepository
from untaped.capabilities.awx.infrastructure.specs import (
    INVENTORY_SOURCE_SPEC,
    INVENTORY_SPEC,
    JOB_TEMPLATE_SPEC,
    SCHEDULE_SPEC,
)
from untaped.capabilities.awx.infrastructure.strategy_resolver import StaticStrategyResolver


def test_inventory_is_mutable_and_ordered():
    assert INVENTORY_SPEC.fidelity == "full"
    assert {r.field for r in INVENTORY_SPEC.fk_refs if r.ordered} == {
        "input_inventories",
        "instance_groups",
    }


def test_recursive_filename():
    parent = IdentityRef(
        kind="InventorySource",
        name="source",
        parent=IdentityRef(kind="Inventory", name="inv", organization="org"),
    )
    assert "org__inv" in resource_filename("Schedule", Metadata(name="night", parent=parent))


def test_text_comparison_is_explicit():
    assert not semantic_equal('{"a": 1}', {"a": 1})
    assert (
        ApplyVerifier().unreflected_fields(
            JOB_TEMPLATE_SPEC, {"extra_vars": {}}, {"extra_vars": "{}"}, fields=("extra_vars",)
        )
        == ()
    )
    assert ApplyVerifier().unreflected_fields(
        JOB_TEMPLATE_SPEC, {"description": {}}, {"description": "{}"}, fields=("description",)
    ) == ("description",)


class Controller:
    """Strict route fixture: constructed detail and base inventory relationships."""

    def __init__(self) -> None:
        self.records: dict[str, dict[int, dict[str, Any]]] = {
            "organizations": {1: {"id": 1, "name": "org"}, 2: {"id": 2, "name": "other"}},
            "inventories": {10: {"id": 10, "name": "inv", "organization": 1, "kind": ""}},
            "inventory_sources": {},
            "schedules": {},
            "job_templates": {},
            "hosts": {},
            "groups": {},
            "workflow_job_templates": {},
            "projects": {},
            "credentials": {},
            "execution_environments": {},
            "instance_groups": {1: {"id": 1, "name": "first"}, 2: {"id": 2, "name": "second"}},
        }
        self.members: dict[tuple[int, str], list[int]] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.sparse_patch_response = False

    def dispatch(self, request: httpx.Request) -> httpx.Response:  # noqa: C901 - HTTP fixture routing
        parts = request.url.path.removeprefix("/api/v2/").strip("/").split("/")
        endpoint = parts[0]
        collection = "inventories" if endpoint == "constructed_inventories" else endpoint
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, body))
        assert not (endpoint == "constructed_inventories" and len(parts) > 2)
        if collection == "unified_job_templates":
            record = self.records["inventory_sources"][int(parts[1])]
            return httpx.Response(200, json={**record, "type": "inventory_source"})
        records = self.records[collection]
        if len(parts) == 3:
            parent_id, child = int(parts[1]), parts[2]
            if child in {"input_inventories", "instance_groups"} or (
                collection == "groups" and child in {"hosts", "children"}
            ):
                ids = self.members.setdefault((parent_id, child), [])
                if request.method == "POST":
                    if body.get("disassociate"):
                        ids.remove(body["id"])
                    elif body["id"] not in ids:
                        ids.append(body["id"])
                    return httpx.Response(204)
                child_collection = {"input_inventories": "inventories", "children": "groups"}.get(
                    child, child
                )
                return httpx.Response(
                    200,
                    json={
                        "results": [self.records[child_collection][i] for i in ids],
                        "next": None,
                    },
                )
            records = self.records[child]
            if request.method == "GET":
                relation = "inventory" if collection == "inventories" else "unified_job_template"
                values = [r for r in records.values() if r.get(relation) == parent_id]
                if "name" in request.url.params:
                    values = [r for r in values if r["name"] == request.url.params["name"]]
                return httpx.Response(200, json={"results": values, "count": len(values)})
            body["inventory" if collection == "inventories" else "unified_job_template"] = parent_id
        if request.method == "GET":
            if len(parts) == 2:
                return httpx.Response(
                    200,
                    json=self.inventory_view(records[int(parts[1])])
                    if endpoint == "constructed_inventories"
                    else records[int(parts[1])],
                )
            values = list(records.values())
            for key, value in request.url.params.items():
                if key in {"name", "id"}:
                    values = [r for r in values if str(r.get(key)) == value]
                elif key == "organization__name":
                    values = [
                        r
                        for r in values
                        if self.records["organizations"][r["organization"]]["name"] == value
                    ]
                elif key == "inventory__name":
                    values = [
                        r
                        for r in values
                        if self.records["inventories"][r["inventory"]]["name"] == value
                    ]
                elif key == "inventory__organization__name":
                    values = [
                        r
                        for r in values
                        if self.records["organizations"][
                            self.records["inventories"][r["inventory"]]["organization"]
                        ]["name"]
                        == value
                    ]
            return httpx.Response(200, json={"results": values, "count": len(values), "next": None})
        if request.method == "DELETE":
            return httpx.Response(202)
        if request.method == "PATCH":
            records[int(parts[1])].update(body)
            if endpoint == "constructed_inventories":
                for source in self.records["inventory_sources"].values():
                    if (
                        source.get("inventory") == int(parts[1])
                        and source.get("source") == "constructed"
                    ):
                        source.update(
                            {
                                k: v
                                for k, v in body.items()
                                if k
                                in {"source_vars", "update_cache_timeout", "limit", "verbosity"}
                            }
                        )
            if self.sparse_patch_response:
                return httpx.Response(200, json={"id": int(parts[1])})
            return httpx.Response(
                200,
                json=self.inventory_view(records[int(parts[1])])
                if endpoint == "constructed_inventories"
                else records[int(parts[1])],
            )
        assert request.method == "POST"
        rid = max(records, default=100) + 1
        records[rid] = {"id": rid, **body}
        if endpoint == "constructed_inventories":
            records[rid].update(kind="constructed")
            self.records["inventory_sources"][rid + 1000] = {
                "id": rid + 1000,
                "name": "Auto-generated",
                "inventory": rid,
                "source": "constructed",
                "source_vars": "{}",
                "update_cache_timeout": 0,
                **{
                    k: v
                    for k, v in body.items()
                    if k in {"source_vars", "update_cache_timeout", "limit", "verbosity"}
                },
            }
        return httpx.Response(201, json=records[rid])

    def inventory_view(self, record: dict[str, Any]) -> dict[str, Any]:
        """Constructed proxy fields read the generated source's physical storage."""
        source = next(
            (
                s
                for s in self.records["inventory_sources"].values()
                if s.get("inventory") == record["id"] and s.get("source") == "constructed"
            ),
            {},
        )
        return {
            **record,
            **{
                k: v
                for k, v in source.items()
                if k in {"source_vars", "update_cache_timeout", "limit", "verbosity"}
            },
        }

    @property
    def writes(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def controller(
    awx_config: AwxConfig,
) -> Iterator[tuple[Controller, BatchMutationEngine, ResourceRepository, FkResolver]]:
    state = Controller()
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        mock.route().mock(side_effect=state.dispatch)
        with AwxClient(awx_config) as client:
            repo = ResourceRepository(client)
            catalog = AwxResourceCatalog()
            fk = FkResolver(repo, catalog)
            yield state, BatchMutationEngine(repo, catalog, fk, StaticStrategyResolver()), repo, fk


def inventory_doc(**spec: Any) -> Resource:
    return Resource(kind="Inventory", metadata=Metadata(name="inv", organization="org"), spec=spec)


def source_doc(**spec: Any) -> Resource:
    return Resource(
        kind="InventorySource",
        metadata=Metadata(
            name="sync", parent=IdentityRef(kind="Inventory", name="inv", organization="org")
        ),
        spec=spec,
    )


@pytest.mark.parametrize("value", [{}, {"a": [2, 1]}, '{"a": 2}'])
def test_source_wire_codec_and_cache_only(controller: Any, value: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "sync",
        "inventory": 10,
        "source": "scm",
        "source_vars": '{"old": true}',
        "update_cache_timeout": 10,
        "update_on_launch": False,
    }
    result = engine.run([source_doc(source_vars=value, update_cache_timeout=0)], write=True)
    assert result.outcomes[0].action == "updated"
    sent = state.writes[0][2]
    assert isinstance(sent["source_vars"], str)
    assert sent["source_vars"] == (value if isinstance(value, str) else json.dumps(value))
    assert sent["update_cache_timeout"] == 0
    assert "update_on_launch" not in sent
    assert len(state.writes) == 1


def test_selected_constructed_hydrates_and_freezes_detail_and_membership_routes(
    controller: Any,
) -> None:
    state, engine, _, _ = controller
    state.records["inventories"][10].update(
        kind="constructed", source_vars="{}", update_cache_timeout=0
    )
    state.records["inventories"][11] = {"id": 11, "name": "same", "organization": 1, "kind": ""}
    state.records["inventories"][12] = {"id": 12, "name": "same", "organization": 2, "kind": ""}
    state.members[(10, "input_inventories")] = [11, 12]
    selected = {
        "id": 10,
        "name": "inv",
        "organization": 1,
        "kind": "constructed",
        "type": "inventory",
    }
    doc = inventory_doc(
        update_cache_timeout=20,
        input_inventories=[
            {"name": "same", "organization": "other"},
            {"name": "same", "organization": "org"},
        ],
    )
    plan = engine.prepare([doc], mode="patch", existing=[selected])
    assert plan.operations[0].existing["update_cache_timeout"] == 0
    assert plan.operations[0].spec.api_path == "constructed_inventories"
    result = engine.execute(plan)
    assert result.outcomes[0].action == "updated"
    assert state.members[(10, "input_inventories")] == [12, 11]
    assert state.writes[0][1] == "/api/v2/constructed_inventories/10/"
    assert all("/hosts/" not in path for _, path, _ in state.calls)
    assert sum(path == "/api/v2/inventories/" for _, path, _ in state.calls) == 2


def test_constructed_proxy_drift_conflicts_before_writes(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventories"][10].update(
        kind="constructed", source_vars="{}", update_cache_timeout=0
    )
    plan = engine.prepare([inventory_doc(update_cache_timeout=20)])
    state.records["inventories"][10]["update_cache_timeout"] = 5
    assert engine.execute(plan).outcomes[0].action == "conflict"
    assert state.writes == []


def test_generated_source_reused_when_restore_name_differs(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventories"][10]["kind"] = "constructed"
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "Auto",
        "inventory": 10,
        "source": "constructed",
        "source_vars": "{}",
    }
    plan = engine.prepare([source_doc(source="constructed", source_vars={"plugin": "constructed"})])
    assert plan.operations[0].target_id == 20
    assert engine.execute(plan).outcomes[0].action == "updated"
    assert [(verb, path) for verb, path, _ in state.writes] == [
        ("PATCH", "/api/v2/inventory_sources/20/")
    ]


def test_generated_source_invalid_configuration_is_prewrite_error(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventories"][10]["kind"] = "constructed"
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "sync",
        "inventory": 10,
        "source": "constructed",
    }
    with pytest.raises(BadRequest, match="only permits"):
        engine.prepare([inventory_doc(description="changed"), source_doc(update_on_launch=True)])
    assert state.writes == []


def test_new_constructed_inventory_reuses_auto_source_and_orders_schedule(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventories"].clear()
    parent = source_doc(source="constructed", source_vars={})
    schedule = Resource(
        kind="Schedule",
        metadata=Metadata(
            name="night",
            parent=IdentityRef(kind="InventorySource", name="sync", parent=parent.metadata.parent),
        ),
        spec={"rrule": "DTSTART:20260908T000000Z RRULE:FREQ=DAILY", "extra_data": {}},
    )
    plan = engine.prepare([schedule, parent, inventory_doc(kind="constructed", source_vars={})])
    result = engine.execute(plan)
    assert [o.action for o in result.outcomes] == ["created", "created", "created"]
    assert len(state.records["inventory_sources"]) == 1
    assert [path for verb, path, _ in state.writes if verb == "POST"] == [
        "/api/v2/constructed_inventories/",
        "/api/v2/inventory_sources/1101/schedules/",
    ]
    assert state.records["schedules"][101]["extra_data"] == {}


def test_source_schedule_export_has_full_ancestry_without_summary(controller: Any) -> None:
    state, _, repo, fk = controller
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "sync",
        "inventory": 10,
        "source": "scm",
    }
    source = SaveResource(repo, fk).from_record(
        INVENTORY_SOURCE_SPEC, state.records["inventory_sources"][20]
    )
    assert source.metadata.parent == IdentityRef(kind="Inventory", name="inv", organization="org")
    schedule = SaveResource(repo, fk).from_record(
        SCHEDULE_SPEC,
        {
            "id": 30,
            "name": "night",
            "unified_job_template": 20,
            "summary_fields": {
                "unified_job_template": {"name": "sync", "unified_job_type": "inventory_source"}
            },
        },
    )
    assert schedule.metadata.parent.parent == source.metadata.parent
    assert "org__inv" in resource_filename("Schedule", schedule.metadata)
    assert fk.resolve_polymorphic(schedule.metadata.parent.model_dump(exclude_none=True)) == (
        "InventorySource",
        20,
    )


def test_ordered_instance_groups_and_scoped_export(controller: Any) -> None:
    state, engine, repo, fk = controller
    state.members[(10, "instance_groups")] = [1, 2]
    result = engine.run([inventory_doc(instance_groups=[2, 1])], write=True)
    assert result.outcomes[0].action == "updated"
    assert state.members[(10, "instance_groups")] == [2, 1]
    saved = SaveResource(repo, fk).from_record(INVENTORY_SPEC, state.records["inventories"][10])
    assert saved.spec["instance_groups"] == ["second", "first"]
    assert "input_inventories" not in saved.spec


def test_existing_parent_dependency_is_explicit(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "sync",
        "inventory": 10,
        "source": "scm",
    }
    docs = [source_doc(update_cache_timeout=0), inventory_doc(description="changed")]
    plan = engine.prepare(docs)
    assert plan.operations[0].dependencies == (1,)
    assert [r.kind for r in topological_sort(docs, catalog=AwxResourceCatalog())] == [
        "Inventory",
        "InventorySource",
    ]


def test_inventory_delete_reports_request_acceptance(controller: Any) -> None:
    state, _, repo, _ = controller
    receipt = DeleteResource(repo)(INVENTORY_SPEC, 10)
    assert receipt.action == "deletion_requested"
    assert 10 in state.records["inventories"]


@pytest.mark.parametrize(
    "field,value", [("update_on_launch", True), ("source", "scm"), ("description", "wrong")]
)
def test_new_constructed_parent_rejects_independent_source_before_writes(
    controller: Any, field: str, value: Any
) -> None:
    state, engine, _, _ = controller
    state.records["inventories"].clear()
    with pytest.raises(BadRequest):
        engine.prepare([inventory_doc(kind="constructed"), source_doc(**{field: value})])
    assert state.writes == []


def test_existing_recursive_source_parent_dependency(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "sync",
        "inventory": 10,
        "source": "scm",
    }
    state.records["schedules"][30] = {"id": 30, "name": "night", "unified_job_template": 20}
    source = source_doc(update_cache_timeout=0)
    schedule = Resource(
        kind="Schedule",
        metadata=Metadata(
            name="night",
            parent=IdentityRef(kind="InventorySource", name="sync", parent=source.metadata.parent),
        ),
        spec={"enabled": False},
    )
    plan = engine.prepare([schedule, source, inventory_doc(description="changed")])
    assert plan.operations[0].dependencies == (1,)
    assert plan.operations[1].dependencies == (2,)


def test_same_source_names_roundtrip_across_inventory_and_organization(controller: Any) -> None:
    state, engine, repo, fk = controller
    state.records["inventories"][11] = {"id": 11, "name": "inv", "organization": 2, "kind": ""}
    state.records["inventories"][12] = {
        "id": 12,
        "name": "different",
        "organization": 1,
        "kind": "",
    }
    docs = []
    filenames = set()
    for source_id, inv_id in [(20, 10), (21, 11), (22, 12)]:
        record = {
            "id": source_id,
            "name": "sync",
            "inventory": inv_id,
            "source": "scm",
            "source_vars": "{}",
            "update_cache_timeout": 0,
        }
        state.records["inventory_sources"][source_id] = record
        saved = SaveResource(repo, fk).from_record(INVENTORY_SOURCE_SPEC, record)
        docs.append(saved)
        filenames.add(resource_filename(saved.kind, saved.metadata))
    assert len(filenames) == 3
    plan = engine.prepare(docs)
    assert [operation.target_id for operation in plan.operations] == [20, 21, 22]
    assert [outcome.action for outcome in engine.execute(plan).outcomes] == ["unchanged"] * 3
    assert state.writes == []


def test_constructed_save_keeps_scoped_input_order_and_generated_allowlist(controller: Any) -> None:
    state, _, repo, fk = controller
    state.records["inventories"][10].update(
        kind="constructed", source_vars='{"groups": {}}', update_cache_timeout=0
    )
    state.records["inventories"][11] = {"id": 11, "name": "input", "organization": 2, "kind": ""}
    state.records["inventories"][12] = {"id": 12, "name": "input", "organization": 1, "kind": ""}
    state.members[(10, "input_inventories")] = [11, 12]
    saved = SaveResource(repo, fk).from_record(INVENTORY_SPEC, state.records["inventories"][10])
    assert saved.spec["input_inventories"] == [
        {"kind": "Inventory", "name": "input", "organization": "other"},
        {"kind": "Inventory", "name": "input", "organization": "org"},
    ]
    assert saved.spec["update_cache_timeout"] == 0
    source = SaveResource(repo, fk).from_record(
        INVENTORY_SOURCE_SPEC,
        {
            "id": 20,
            "name": "auto",
            "inventory": 10,
            "source": "constructed",
            "source_vars": "{}",
            "update_on_launch": True,
            "overwrite": True,
            "status": "successful",
        },
    )
    assert source.spec == {"source": "constructed", "source_vars": "{}"}


@pytest.mark.parametrize(
    "kind,endpoint,field",
    [
        ("Inventory", "inventories", "variables"),
        ("Host", "hosts", "variables"),
        ("Group", "groups", "variables"),
        ("WorkflowJobTemplate", "workflow_job_templates", "extra_vars"),
        ("JobTemplate", "job_templates", "extra_vars"),
    ],
)
def test_known_mapping_fields_have_real_text_wire_and_exact_semantics(
    controller: Any, kind: str, endpoint: str, field: str
) -> None:
    state, engine, _, _ = controller
    record = {
        "id": 10 if kind == "Inventory" else 50,
        "name": "inv" if kind == "Inventory" else "sample",
        "organization": 1,
        "inventory": 10,
        field: '{"remove": true, "order": [1, 2]}',
    }
    if kind == "Inventory":
        record["kind"] = ""
    state.records[endpoint][record["id"]] = record
    metadata = Metadata(
        name=record["name"],
        organization="org" if kind not in {"Host", "Group"} else None,
        parent=IdentityRef(kind="Inventory", name="inv", organization="org")
        if kind in {"Host", "Group"}
        else None,
    )
    doc = Resource(kind=kind, metadata=metadata, spec={field: {"order": [2, 1]}})
    plan = engine.prepare([doc], mode="patch", existing=[record])
    assert json.loads(plan.operations[0].payload[field]) == {"order": [2, 1]}
    result = engine.execute(plan)
    assert result.outcomes[0].action == "updated"
    assert json.loads(state.writes[-1][2][field]) == {"order": [2, 1]}


def test_missing_schedule_ancestry_is_not_exported_as_restorable(controller: Any) -> None:
    _, _, repo, fk = controller
    with pytest.raises(BadRequest, match="ancestry"):
        SaveResource(repo, fk).from_record(SCHEDULE_SPEC, {"id": 30, "name": "night"})


def test_schedule_export_fetches_parent_type_when_all_summaries_are_missing(
    controller: Any,
) -> None:
    state, _, repo, fk = controller
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "sync",
        "inventory": 10,
        "source": "scm",
    }
    saved = SaveResource(repo, fk).from_record(
        SCHEDULE_SPEC, {"id": 30, "name": "night", "unified_job_template": 20}
    )
    assert saved.metadata.parent == IdentityRef(
        kind="InventorySource",
        name="sync",
        parent=IdentityRef(kind="Inventory", name="inv", organization="org"),
    )


def test_generated_source_cannot_be_deleted_independently(controller: Any) -> None:
    state, _, repo, _ = controller
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "auto",
        "inventory": 10,
        "source": "constructed",
    }
    with pytest.raises(BadRequest, match="cannot be deleted independently"):
        DeleteResource(repo)(INVENTORY_SOURCE_SPEC, 20)
    assert state.writes == []


def test_generated_source_aliases_cannot_schedule_two_writes_to_same_id(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventories"][10]["kind"] = "constructed"
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "auto",
        "inventory": 10,
        "source": "constructed",
    }
    first = source_doc(source="constructed", update_cache_timeout=1)
    second = first.model_copy(deep=True)
    second.metadata.name = "different-alias"
    with pytest.raises(MutationConflict, match="duplicate resolved target"):
        engine.prepare([first, second])
    assert state.writes == []


def test_inventory_kind_change_is_rejected_before_write(controller: Any) -> None:
    state, engine, _, _ = controller
    with pytest.raises(BadRequest, match="kind cannot change"):
        engine.prepare([inventory_doc(kind="smart")])
    assert state.writes == []


def test_mapping_text_semantic_equality_avoids_spurious_write(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventories"][10]["variables"] = "a: 1\norder: [2, 1]\n"
    plan = engine.prepare([inventory_doc(variables={"order": [2, 1], "a": 1})])
    assert plan.operations[0].payload == {}
    assert engine.execute(plan).outcomes[0].action == "unchanged"
    assert state.writes == []


def test_new_standard_parent_cannot_create_generated_source(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventories"].clear()
    with pytest.raises(BadRequest, match="constructed sources require"):
        engine.prepare([source_doc(source="constructed"), inventory_doc()])
    assert state.writes == []


def test_constructed_sparse_write_response_verifies_through_frozen_proxy(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["inventories"][10].update(kind="constructed", source_vars="{}")
    state.sparse_patch_response = True
    result = engine.run([inventory_doc(source_vars={"groups": {"kept": "true"}})], write=True)
    assert result.outcomes[0].action == "updated"
    assert state.calls[-1][:2] == ("GET", "/api/v2/constructed_inventories/10/")
    assert result.outcomes[0].unverified is False


def test_constructed_host_filter_does_not_leak_into_saved_restore(controller: Any) -> None:
    state, engine, repo, fk = controller
    state.records["inventories"][10].update(kind="constructed", host_filter=None, source_vars="{}")
    saved = SaveResource(repo, fk).from_record(INVENTORY_SPEC, state.records["inventories"][10])
    assert "host_filter" not in saved.spec
    assert engine.run([saved], write=True).outcomes[0].action == "unchanged"
    assert state.writes == []


@pytest.mark.parametrize("parent_first", [False, True])
@pytest.mark.parametrize("second_parent_org", ["org", None])
def test_new_generated_source_aliases_fail_before_parent_creation(
    controller: Any, parent_first: bool, second_parent_org: str | None
) -> None:
    state, engine, _, _ = controller
    state.records["inventories"].clear()
    first = source_doc(update_cache_timeout=1)
    second = source_doc(update_cache_timeout=2)
    second.metadata.name = "different-alias"
    second.metadata.parent = IdentityRef(
        kind="Inventory", name="inv", organization=second_parent_org
    )
    inventory = inventory_doc(kind="constructed")
    docs = [inventory, first, second] if parent_first else [first, second, inventory]
    with pytest.raises(MutationConflict, match="duplicate parent-owned target"):
        engine.prepare(docs)
    assert state.writes == []
    assert state.records["inventories"] == {}
    assert state.records["inventory_sources"] == {}


def test_generated_sources_on_distinct_new_parents_are_not_duplicate_targets(
    controller: Any,
) -> None:
    state, engine, _, _ = controller
    state.records["inventories"].clear()
    first_inventory = inventory_doc(kind="constructed")
    second_inventory = inventory_doc(kind="constructed")
    second_inventory.metadata.organization = "other"
    first_source = source_doc(update_cache_timeout=1)
    second_source = source_doc(update_cache_timeout=2)
    second_source.metadata.parent = IdentityRef(kind="Inventory", name="inv", organization="other")
    plan = engine.prepare([first_source, second_source, first_inventory, second_inventory])
    result = engine.execute(plan)
    assert all(outcome.action == "created" for outcome in result.outcomes)
    assert result.outcomes[0].id != result.outcomes[1].id
    assert len(state.records["inventory_sources"]) == 2


@pytest.mark.parametrize(
    "field,before,drift,desired",
    [("update_cache_timeout", 0, 5, 20), ("description", "old", "remote", "edited")],
)
def test_constructed_editor_retains_full_display_snapshot_and_aborts_batch(
    controller: Any, field: str, before: Any, drift: Any, desired: Any
) -> None:
    import yaml

    from untaped.capabilities.awx.application.edit_resources import EditResources
    from untaped.capabilities.awx.application.selection import SelectedResource

    state, engine, repo, fk = controller
    state.records["inventories"][10].update(
        kind="constructed", update_cache_timeout=0, description="old"
    )
    state.records["inventories"][11] = {
        "id": 11,
        "name": "second",
        "organization": 1,
        "kind": "",
        "description": "old",
    }
    selected = [
        SelectedResource(
            "Inventory",
            id_,
            record["name"],
            {},
            {k: v for k, v in record.items() if k != "update_cache_timeout"},
        )
        for id_, record in state.records["inventories"].items()
    ]
    editor = EditResources(INVENTORY_SPEC, selected, SaveResource(repo, fk))
    documents = list(yaml.safe_load_all(editor.render()))
    assert documents[0]["spec"][field] == before
    state.records["inventories"][10][field] = drift
    documents[0]["spec"][field] = desired
    documents[1]["spec"]["description"] = "valid"
    resources, retained = editor.parse(yaml.safe_dump_all(documents))
    result = engine.execute(
        engine.prepare(
            resources,
            mode="edit",
            existing=retained,
            membership_snapshots=editor.membership_snapshots,
        )
    )
    assert [o.action for o in result.outcomes] == ["conflict", "skipped"]
    assert state.writes == []


@pytest.mark.parametrize(
    "value", ["external", 21, {"name": "external"}, {"name": "external", "organization": "other"}]
)
def test_fk_representations_cannot_escape_required_scope(controller: Any, value: Any) -> None:
    from untaped.capabilities.awx.application.apply_planner import resolve_fk_value
    from untaped.capabilities.awx.errors import AwxApiError

    state, _, _, fk = controller
    state.records["projects"][21] = {"id": 21, "name": "external", "organization": 2}
    with pytest.raises(AwxApiError):
        resolve_fk_value("Project", value, scope={"organization": "org"}, fk=fk)
    assert state.writes == []


@pytest.mark.parametrize("kind", ["Host", "Group"])
def test_mapping_child_reference_uses_inventory_ancestry(controller: Any, kind: str) -> None:
    from untaped.capabilities.awx.application.apply_planner import resolve_fk_value
    from untaped.capabilities.awx.errors import AwxApiError

    state, _, _, fk = controller
    state.records["inventories"][11] = {"id": 11, "name": "outside", "organization": 2, "kind": ""}
    collection = "hosts" if kind == "Host" else "groups"
    state.records[collection][20] = {"id": 20, "name": "same", "inventory": 10}
    state.records[collection][21] = {"id": 21, "name": "same", "inventory": 11}
    reference = {
        "name": "same",
        "parent": {"kind": "Inventory", "name": "inv", "organization": "org"},
    }
    assert resolve_fk_value(kind, reference, scope={"inventory": "inv"}, fk=fk) == 20
    assert fk.resolve_polymorphic({"kind": kind, **reference}) == (kind, 20)
    reference["parent"]["name"] = "outside"
    with pytest.raises(AwxApiError):
        resolve_fk_value(kind, reference, scope={"inventory": "inv"}, fk=fk)


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize(
    "field,first,second",
    [
        ("update_cache_timeout", 10, 20),
        ("source_vars", {"a": 1}, '{"a":2}'),
        ("limit", "first", "second"),
        ("verbosity", 1, 2),
    ],
)
def test_constructed_proxy_source_contradictions_fail_before_writes(
    controller: Any, deferred: bool, field: str, first: Any, second: Any
) -> None:
    state, engine, _, _ = controller
    if deferred:
        state.records["inventories"].clear()
    else:
        state.records["inventories"][10].update(kind="constructed", update_cache_timeout=0)
        state.records["inventory_sources"][20] = {
            "id": 20,
            "name": "sync",
            "inventory": 10,
            "source": "constructed",
            "update_cache_timeout": 0,
        }
    with pytest.raises(MutationConflict, match=r"conflicting.*field"):
        engine.prepare(
            [inventory_doc(kind="constructed", **{field: first}), source_doc(**{field: second})]
        )
    assert state.writes == []


@pytest.mark.parametrize("mode", ["patch", "edit"])
@pytest.mark.parametrize("body", [{"status": "failed"}, {"description": "new", "status": "failed"}])
def test_explicit_readonly_patch_rejects_whole_batch(controller: Any, mode: str, body: Any) -> None:
    state, engine, _, _ = controller
    state.records["projects"][20] = {
        "id": 20,
        "name": "project",
        "organization": 1,
        "description": "old",
        "status": "successful",
    }
    resources = [
        inventory_doc(description="valid"),
        Resource(kind="Project", metadata=Metadata(name="project", organization="org"), spec=body),
    ]
    with pytest.raises(BadRequest, match=r"read.only.*status"):
        engine.prepare(resources, mode=mode)
    assert state.writes == []


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("same", [False, True])
def test_constructed_aliases_preserve_compatible_and_independent_fields(
    controller: Any, deferred: bool, same: bool
) -> None:
    state, engine, repo, _ = controller
    if deferred:
        state.records["inventories"].clear()
    else:
        state.records["inventories"][10].update(kind="constructed", description="old")
        state.records["inventory_sources"][20] = {
            "id": 20,
            "name": "sync",
            "inventory": 10,
            "source": "constructed",
            "source_vars": "{}",
            "update_cache_timeout": 0,
        }
    parent = inventory_doc(kind="constructed", description="kept", source_vars={"a": 1})
    child = source_doc(update_cache_timeout=30, **({"source_vars": '{"a": 1}'} if same else {}))
    result = engine.execute(engine.prepare([child, parent]), parallel=2)
    assert all(o.action in {"created", "updated"} and not o.unverified for o in result.outcomes)
    inventory = repo.get(INVENTORY_SPEC, result.outcomes[1].id)
    assert inventory["description"] == "kept"
    assert semantic_equal(inventory["source_vars"], {"a": 1}, structured_text=True)
    assert inventory["update_cache_timeout"] == 30
    generated = next(iter(state.records["inventory_sources"].values()))
    assert generated["update_cache_timeout"] == inventory["update_cache_timeout"]


@pytest.mark.parametrize("scope", [None, "org", "other"])
@pytest.mark.parametrize("same_org", [True, False])
def test_deferred_mapping_fk_retains_job_template_scope(
    controller: Any, scope: str | None, same_org: bool
) -> None:
    from untaped.capabilities.awx.errors import AwxApiError

    state, engine, _, _ = controller
    org = "org" if same_org else "other"
    project = Resource(
        kind="Project",
        metadata=Metadata(name="new", organization=org),
        spec={"description": "project"},
    )
    reference = {"name": "new", **({"organization": scope} if scope is not None else {})}
    template = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="job", organization="org"),
        spec={"project": reference},
    )
    if not same_org or scope == "other":
        with pytest.raises(AwxApiError):
            engine.prepare([template, project])
        assert state.writes == []
    else:
        result = engine.execute(engine.prepare([template, project]))
        assert [o.action for o in result.outcomes] == ["created", "created"]
        assert (
            state.records["job_templates"][result.outcomes[0].id]["project"]
            == result.outcomes[1].id
        )


@pytest.mark.parametrize("mode", ["patch", "edit"])
@pytest.mark.parametrize("source", [True, False])
def test_explicit_strategy_readonly_fields_reject_batch(
    controller: Any, mode: str, source: bool
) -> None:
    state, engine, _, _ = controller
    state.records["inventories"][10].update(kind="constructed", source_vars="{}")
    state.records["inventory_sources"][20] = {
        "id": 20,
        "name": "sync",
        "inventory": 10,
        "source": "constructed",
        "source_vars": "{}",
    }
    resource = (
        source_doc(source="constructed", update_cache_timeout=20)
        if source
        else inventory_doc(host_filter=None, description="valid")
    )
    with pytest.raises(BadRequest, match=r"read.only"):
        engine.prepare([resource], mode=mode)
    assert state.writes == []


def test_generated_source_editor_omits_readonly_type_but_edits_cache(controller: Any) -> None:
    import yaml

    from untaped.capabilities.awx.application.edit_resources import EditResources
    from untaped.capabilities.awx.application.selection import SelectedResource

    state, engine, repo, fk = controller
    state.records["inventories"][10]["kind"] = "constructed"
    record = {
        "id": 20,
        "name": "sync",
        "inventory": 10,
        "source": "constructed",
        "source_vars": "{}",
        "update_cache_timeout": 0,
    }
    state.records["inventory_sources"][20] = record
    editor = EditResources(
        INVENTORY_SOURCE_SPEC,
        [SelectedResource("InventorySource", 20, "sync", {}, record)],
        SaveResource(repo, fk),
    )
    documents = list(yaml.safe_load_all(editor.render()))
    assert "source" not in documents[0]["spec"]
    documents[0]["spec"]["update_cache_timeout"] = 20
    resources, selected = editor.parse(yaml.safe_dump_all(documents))
    result = engine.execute(
        engine.prepare(
            resources,
            mode="edit",
            existing=selected,
            membership_snapshots=editor.membership_snapshots,
        )
    )
    assert result.outcomes[0].action == "updated"
    assert state.records["inventory_sources"][20]["update_cache_timeout"] == 20


@pytest.mark.parametrize(
    "kind,field,collection", [("Host", "hosts", "hosts"), ("Group", "children", "groups")]
)
@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("outside", [False, True])
def test_mapping_memberships_retain_scope_for_existing_and_deferred_children(
    controller: Any, kind: str, field: str, collection: str, deferred: bool, outside: bool
) -> None:
    from untaped.capabilities.awx.errors import AwxApiError

    state, engine, _, _ = controller
    state.records["inventories"][11] = {"id": 11, "name": "outside", "organization": 2, "kind": ""}
    parent = IdentityRef(kind="Inventory", name="inv", organization="org")
    child_parent = (
        IdentityRef(kind="Inventory", name="outside", organization="other") if outside else parent
    )
    child = Resource(
        kind=kind,
        metadata=Metadata(name="child", parent=child_parent),
        spec={"description": "child"},
    )
    group = Resource(
        kind="Group",
        metadata=Metadata(name="parent", parent=parent),
        spec={field: [{"name": "child", "parent": child_parent.model_dump(exclude_none=True)}]},
    )
    state.records["groups"][30] = {"id": 30, "name": "parent", "inventory": 10}
    if not deferred:
        state.records[collection][20] = {
            "id": 20,
            "name": "child",
            "inventory": 11 if outside else 10,
            "description": "child",
        }
    if outside:
        with pytest.raises(AwxApiError):
            engine.prepare([group, child])
        assert state.writes == []
    else:
        result = engine.execute(engine.prepare([group, child]))
        assert all(o.action in {"created", "updated", "unchanged"} for o in result.outcomes)
        assert state.members[(30, field)] == [result.outcomes[1].id]


def test_deferred_constructed_input_inventory_remains_intentionally_cross_org(
    controller: Any,
) -> None:
    state, engine, _, _ = controller
    state.records["inventories"].clear()
    child = Resource(
        kind="Inventory", metadata=Metadata(name="outside", organization="other"), spec={"kind": ""}
    )
    parent = inventory_doc(
        kind="constructed", input_inventories=[{"name": "outside", "organization": "other"}]
    )
    result = engine.execute(engine.prepare([parent, child]))
    assert [o.action for o in result.outcomes] == ["created", "created"]
    assert state.members[(result.outcomes[0].id, "input_inventories")] == [result.outcomes[1].id]


@pytest.mark.parametrize(
    "reference",
    [
        {"name": "same", "parent": {"kind": "Project", "name": "inv"}},
        {
            "name": "same",
            "parent": {
                "kind": "Inventory",
                "name": "inv",
                "parent": {"kind": "Organization", "name": "org"},
            },
        },
        {
            "name": "same",
            "organization": "other",
            "parent": {"kind": "Inventory", "name": "inv", "organization": "org"},
        },
    ],
)
def test_unsupported_or_conflicting_child_ancestry_is_never_dropped(
    controller: Any, reference: Any
) -> None:
    from untaped.capabilities.awx.application.apply_planner import resolve_fk_value

    state, _, _, fk = controller
    state.records["hosts"][20] = {"id": 20, "name": "same", "inventory": 10}
    with pytest.raises(BadRequest, match="ancestry"):
        resolve_fk_value("Host", reference, scope=None, fk=fk)
    assert state.writes == []


def test_apply_import_still_drops_known_readonly_fields(controller: Any) -> None:
    state, engine, _, _ = controller
    state.records["projects"][20] = {
        "id": 20,
        "name": "project",
        "organization": 1,
        "description": "old",
        "status": "successful",
    }
    doc = Resource(
        kind="Project",
        metadata=Metadata(name="project", organization="org"),
        spec={"description": "new", "status": "failed"},
    )
    result = engine.run([doc], write=True)
    assert result.outcomes[0].action == "updated"
    assert state.writes == [("PATCH", "/api/v2/projects/20/", {"description": "new"})]


@pytest.mark.parametrize(
    "kind,collection",
    [("Project", "projects"), ("Inventory", "inventories"), ("Credential", "credentials")],
)
@pytest.mark.parametrize("explicit", [False, True])
def test_mapping_fk_refines_required_scope_without_global_ambiguity(
    controller: Any, kind: str, collection: str, explicit: bool
) -> None:
    from untaped.capabilities.awx.application.apply_planner import resolve_fk_value

    state, _, _, fk = controller
    state.records[collection][20] = {"id": 20, "name": "same", "organization": 1}
    state.records[collection][21] = {"id": 21, "name": "same", "organization": 2}
    reference = {"name": "same", **({"organization": "org"} if explicit else {})}
    assert resolve_fk_value(kind, reference, scope={"organization": "org"}, fk=fk) == 20
    assert state.writes == []
