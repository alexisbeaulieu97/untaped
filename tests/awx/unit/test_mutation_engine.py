"""Public tests for the AWX batch mutation and selection APIs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast, get_type_hints

import pytest

from untaped.capabilities.awx.application.mutation_engine import (
    BatchMutationEngine,
    MutationConflict,
    _PlanningFkResolver,
)
from untaped.capabilities.awx.application.mutation_types import DeferredReference
from untaped.capabilities.awx.application.ports import (
    Catalog,
    FkResolver,
    RawHttpResourceClient,
    StrategyResolver,
)
from untaped.capabilities.awx.domain import FkRef, Metadata, Resource, ResourceSpec, ServerRecord
from untaped.capabilities.awx.domain.outcomes import DeleteReceipt
from untaped.capabilities.awx.infrastructure.specs import PROJECT_SPEC
from untaped.capabilities.awx.infrastructure.strategies import DefaultApplyStrategy


class _Catalog:
    def __init__(self, *specs: ResourceSpec) -> None:
        self.specs = {spec.kind: spec for spec in specs}

    def get(self, kind: str) -> ResourceSpec:
        return self.specs[kind]

    def kinds(self) -> tuple[str, ...]:
        return tuple(self.specs)

    def by_cli_name(self, cli_name: str) -> ResourceSpec:
        raise KeyError(cli_name)


class _Fk:
    def __init__(self, values: dict[tuple[str, str], int]) -> None:
        self.values = values

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        return self.values[(kind, name)]

    def id_to_name(self, kind: str, id_: int) -> str:
        for (candidate_kind, name), candidate_id in self.values.items():
            if candidate_kind == kind and candidate_id == id_:
                return name
        raise KeyError((kind, id_))

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        return value["kind"], self.name_to_id(value["kind"], value["name"])

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        return None


class _Client:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = {int(record["id"]): dict(record) for record in records}
        self.find_calls = 0
        self.writes: list[tuple[str, int | None, dict[str, Any]]] = []

    def list(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from self.records.values()

    def get(self, spec: ResourceSpec, id_: int) -> ServerRecord:
        return ServerRecord(**self.records[id_])

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        self.find_calls += 1
        name = params.get("name")
        for record in self.records.values():
            if record.get("name") == name:
                return ServerRecord(**record)
        return None

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        return self.find(spec, params={"name": name})

    def create(self, spec: ResourceSpec, payload: Any) -> ServerRecord:
        values = payload.model_dump()
        new_id = max(self.records, default=0) + 1
        self.records[new_id] = {"id": new_id, **values}
        self.writes.append(("create", None, values))
        return ServerRecord(**self.records[new_id])

    def update(self, spec: ResourceSpec, id_: int, payload: Any) -> ServerRecord:
        values = payload.model_dump()
        self.records[id_].update(values)
        self.writes.append(("update", id_, values))
        return ServerRecord(**self.records[id_])

    def delete(self, spec: ResourceSpec, id_: int) -> DeleteReceipt:
        del self.records[id_]
        return DeleteReceipt(action="deleted")

    def action(
        self,
        spec: ResourceSpec,
        id_: int,
        action: str,
        payload: Any = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def paginate_path(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        raise NotImplementedError

    def request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> str:
        raise NotImplementedError

    def sub_endpoint_request(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        method: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def paginate_sub_endpoint(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return iter(())


class _Strategy(DefaultApplyStrategy):
    def find_existing(self, spec: Any, identity: dict[str, Any], *, client: Any, fk: Any) -> Any:
        return client.find(spec, params={"name": str(identity["name"])})

    def create(
        self,
        spec: Any,
        payload: dict[str, Any],
        identity: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        return kwargs["client"].create(spec, _payload(payload)).model_dump()

    def update(
        self,
        spec: Any,
        existing: dict[str, Any],
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        return kwargs["client"].update(spec, existing["id"], _payload(payload)).model_dump()


class _Strategies:
    def __init__(self) -> None:
        self.strategy = _Strategy()

    def get(self, name: str) -> _Strategy:
        return self.strategy


def _payload(values: dict[str, Any]) -> Any:
    from untaped.capabilities.awx.domain import WritePayload

    return WritePayload(**values)


def _engine(client: _Client) -> BatchMutationEngine:
    return BatchMutationEngine(
        client=cast(RawHttpResourceClient, client),
        catalog=cast(Catalog, _Catalog(PROJECT_SPEC)),
        fk=cast(FkResolver, _Fk({("Organization", "Default"): 1})),
        strategies=cast(StrategyResolver, _Strategies()),
    )


def _project(name: str, description: str) -> Resource:
    return Resource(
        kind="Project",
        metadata=Metadata(name=name, organization="Default"),
        spec={"description": description, "scm_type": "git"},
    )


def test_prepare_freezes_existing_id_and_execute_rechecks_before_writes() -> None:
    client = _Client([{"id": 7, "name": "one", "description": "old"}])
    engine = _engine(client)
    plan = engine.prepare([_project("one", "new")])
    assert plan.operations[0].target_id == 7

    client.records[7]["description"] = "changed-out-of-band"
    result = engine.execute(plan)

    assert client.writes == []
    assert result.outcomes[0].action == "conflict"
    assert result.outcomes[0].id == 7


def test_prepare_rejects_create_for_patch_mode() -> None:
    engine = _engine(_Client([]))
    with pytest.raises(MutationConflict, match="does not exist"):
        engine.prepare([_project("missing", "new")], mode="patch")


def test_secret_values_are_redacted_in_plan_presentation() -> None:
    from untaped.capabilities.awx.infrastructure.specs import JOB_TEMPLATE_SPEC

    client = _Client([{"id": 7, "name": "one", "organization": 1, "webhook_key": "old"}])
    engine = BatchMutationEngine(
        client=cast(RawHttpResourceClient, client),
        catalog=cast(Catalog, _Catalog(JOB_TEMPLATE_SPEC)),
        fk=cast(FkResolver, _Fk({("Organization", "Default"): 1})),
        strategies=cast(StrategyResolver, _Strategies()),
    )
    plan = engine.prepare(
        [
            Resource(
                kind="JobTemplate",
                metadata=Metadata(name="one", organization="Default"),
                spec={"webhook_key": "new-secret"},
            )
        ]
    )
    assert "new-secret" not in repr(plan)
    assert plan.operations[0].presentation_payload["webhook_key"] == "<redacted>"


def test_planned_fk_reference_does_not_confuse_negative_literal_values() -> None:
    parent_spec = ResourceSpec(
        kind="Parent",
        identity_keys=("name",),
        canonical_fields=("description",),
    )
    child_spec = ResourceSpec(
        kind="Child",
        identity_keys=("name",),
        canonical_fields=("parent", "offset"),
        fk_refs=(FkRef(field="parent", kind="Parent"),),
    )
    client = _Client([])
    catalog = _Catalog(parent_spec, child_spec)
    fk = _Fk({})
    engine = BatchMutationEngine(
        client=cast(RawHttpResourceClient, client),
        catalog=cast(Catalog, catalog),
        fk=cast(FkResolver, fk),
        strategies=cast(StrategyResolver, _Strategies()),
    )
    parent = Resource(kind="Parent", metadata=Metadata(name="new-parent"), spec={})
    child = Resource(
        kind="Child",
        metadata=Metadata(name="new-child"),
        spec={"parent": "new-parent", "offset": -1, "variables": {"literal": -2}},
    )
    plan = engine.prepare([parent, child])

    assert plan.operations[1].dependencies == (0,)
    assert plan.operations[1].payload["offset"] == -1
    assert plan.operations[1].payload["variables"] == {"literal": -2}


def test_planned_fk_resolver_advertises_typed_deferred_reference() -> None:
    return_type = get_type_hints(_PlanningFkResolver.name_to_id)["return"]

    assert return_type == int | DeferredReference


def test_execute_binds_created_id_for_later_reference() -> None:
    parent_spec = ResourceSpec(
        kind="Parent",
        identity_keys=("name",),
        canonical_fields=("description",),
    )
    child_spec = ResourceSpec(
        kind="Child",
        identity_keys=("name",),
        canonical_fields=("parent",),
        fk_refs=(FkRef(field="parent", kind="Parent"),),
    )
    client = _Client([])
    engine = BatchMutationEngine(
        client=cast(RawHttpResourceClient, client),
        catalog=cast(Catalog, _Catalog(parent_spec, child_spec)),
        fk=cast(FkResolver, _Fk({})),
        strategies=cast(StrategyResolver, _Strategies()),
    )
    result = engine.execute(
        engine.prepare(
            [
                Resource(kind="Parent", metadata=Metadata(name="new-parent"), spec={}),
                Resource(
                    kind="Child",
                    metadata=Metadata(name="new-child"),
                    spec={"parent": "new-parent"},
                ),
            ]
        )
    )
    assert [outcome.action for outcome in result.outcomes] == ["created", "created"], result
    assert client.records[2]["parent"] == 1


def test_execute_uses_prepared_fk_even_if_name_resolution_changes() -> None:
    spec = ResourceSpec(
        kind="Child",
        canonical_fields=("parent",),
        identity_keys=("name",),
        fk_refs=(FkRef(field="parent", kind="Parent"),),
    )
    client = _Client([{"id": 7, "name": "child", "parent": 20}])
    fk = _Fk({("Parent", "parent"): 21})
    engine = BatchMutationEngine(
        client=cast(RawHttpResourceClient, client),
        catalog=cast(Catalog, _Catalog(spec)),
        fk=cast(FkResolver, fk),
        strategies=cast(StrategyResolver, _Strategies()),
    )
    plan = engine.prepare(
        [Resource(kind="Child", metadata=Metadata(name="child"), spec={"parent": "parent"})]
    )
    fk.values[("Parent", "parent")] = 22
    result = engine.execute(plan)
    assert result.outcomes[0].action == "updated"
    assert client.records[7]["parent"] == 21


def test_prepare_rejects_placeholder_create_before_any_write() -> None:
    spec = ResourceSpec(
        kind="Secret",
        canonical_fields=("password",),
        identity_keys=("name",),
        secret_paths=("password",),
    )
    client = _Client([])
    engine = BatchMutationEngine(
        client=cast(RawHttpResourceClient, client),
        catalog=cast(Catalog, _Catalog(spec)),
        fk=cast(FkResolver, _Fk({})),
        strategies=cast(StrategyResolver, _Strategies()),
    )
    from untaped.capabilities.awx.errors import BadRequest

    with pytest.raises(BadRequest, match="placeholder"):
        engine.prepare(
            [
                Resource(
                    kind="Secret", metadata=Metadata(name="bad"), spec={"password": "$encrypted$"}
                )
            ]
        )
    assert not client.writes


class _MembershipClient(_Client):
    def __init__(self, records: list[dict[str, Any]]) -> None:
        super().__init__(records)
        self.members: dict[tuple[int, str], list[int]] = {}
        self.ignore_membership = False
        self.nested_posts: list[str] = []

    def paginate_sub_endpoint(
        self, spec: ResourceSpec, record_id: int, sub_endpoint: str, **kwargs: Any
    ) -> Iterator[dict[str, Any]]:
        return iter(
            {"id": id_, "name": str(id_)} for id_ in self.members.get((record_id, sub_endpoint), [])
        )

    def sub_endpoint_request(
        self, spec: ResourceSpec, record_id: int, sub_endpoint: str, method: str, **kwargs: Any
    ) -> dict[str, Any]:
        if not self.ignore_membership:
            data = kwargs["json"]
            members = self.members.setdefault((record_id, sub_endpoint), [])
            if data.get("disassociate"):
                if data["id"] in members:
                    members.remove(data["id"])
            elif data["id"] not in members:
                members.append(data["id"])
        return {}

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method == "GET":
            return {"results": []}
        assert json is not None
        self.nested_posts.append(path)
        return self.create(PROJECT_SPEC, _payload(json)).model_dump()


def _group_engine(client: _MembershipClient) -> BatchMutationEngine:
    from untaped.capabilities.awx.infrastructure.specs import GROUP_SPEC
    from untaped.capabilities.awx.infrastructure.strategy_resolver import StaticStrategyResolver

    return BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(GROUP_SPEC)),
        cast(FkResolver, _Fk({("Inventory", "prod"): 30})),
        StaticStrategyResolver(),
    )


def _group(name: str, children: list[str]) -> Resource:
    from untaped.capabilities.awx.domain.envelope import IdentityRef

    return Resource(
        kind="Group",
        metadata=Metadata(name=name, parent=IdentityRef(kind="Inventory", name="prod")),
        spec={"children": children},
    )


def test_group_membership_cycles_are_not_body_dependencies() -> None:
    client = _MembershipClient([])
    engine = _group_engine(client)
    plan = engine.prepare([_group("first", ["second"]), _group("second", ["first"])])
    assert [op.dependencies for op in plan.operations] == [(), ()]
    result = engine.execute(plan)
    assert [row.action for row in result.outcomes] == ["created", "created"]
    assert client.members == {(1, "children"): [2], (2, "children"): [1]}
    assert [op.target_id for op in plan.operations] == [None, None]
    assert isinstance(plan.operations[0].membership_plans[0].desired_ids[0], DeferredReference)


def test_nested_create_uses_frozen_parent_id() -> None:
    from untaped.capabilities.awx.infrastructure.specs import GROUP_SPEC
    from untaped.capabilities.awx.infrastructure.strategy_resolver import StaticStrategyResolver

    fk = _Fk({("Inventory", "prod"): 30})
    client = _MembershipClient([])
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(GROUP_SPEC)),
        cast(FkResolver, fk),
        StaticStrategyResolver(),
    )
    plan = engine.prepare([_group("first", [])])
    fk.values[("Inventory", "prod")] = 40
    result = engine.execute(plan)
    assert result.outcomes[0].action == "created"
    assert client.nested_posts == ["inventories/30/groups/"]


def test_empty_membership_snapshot_is_rechecked_before_any_write() -> None:
    spec = ResourceSpec(
        kind="Item",
        canonical_fields=("description",),
        identity_keys=("name",),
        fk_refs=(FkRef(field="members", kind="Item", multi=True, sub_endpoint="members"),),
    )
    client = _MembershipClient([{"id": 1, "name": "first", "description": "old"}])
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    plan = engine.prepare(
        [
            Resource(
                kind="Item",
                metadata=Metadata(name="first"),
                spec={"description": "new", "members": []},
            )
        ]
    )
    client.members[(1, "members")] = [99]
    result = engine.execute(plan)
    assert result.outcomes[0].action == "conflict"
    assert client.writes == []


def test_successful_body_with_unreflected_membership_is_partial() -> None:
    client = _MembershipClient([])
    client.ignore_membership = True
    result = _group_engine(client).run(
        [_group("first", ["second"]), _group("second", [])], write=True
    )
    assert result.outcomes[0].action == "partial"
    assert result.outcomes[0].id == 1
    assert len(client.writes) == 2


@pytest.mark.parametrize(
    "desired,observed", [({}, {"old": 1}), ({"keep": 1}, {"keep": 1, "old": 2}), ([2, 1], [1, 2])]
)
def test_body_replacements_must_converge_exactly(desired: Any, observed: Any) -> None:
    class RefusingClient(_Client):
        def update(self, spec: ResourceSpec, id_: int, payload: Any) -> ServerRecord:
            self.writes.append(("update", id_, payload.model_dump()))
            return ServerRecord(**self.records[id_])

    spec = ResourceSpec(kind="Item", canonical_fields=("value",), identity_keys=("name",))
    client = RefusingClient([{"id": 1, "name": "one", "value": observed}])
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    result = engine.run(
        [Resource(kind="Item", metadata=Metadata(name="one"), spec={"value": desired})], write=True
    )
    assert result.outcomes[0].action == "partial"
    assert client.writes[0][2] == {"value": desired}


def test_selected_target_is_fixed_by_id_even_with_same_named_other_record() -> None:
    from untaped.capabilities.awx.application.selection import SelectedResource

    spec = ResourceSpec(kind="Item", canonical_fields=("description",), identity_keys=("name",))
    client = _Client(
        [
            {"id": 1, "name": "same", "description": "one"},
            {"id": 2, "name": "same", "description": "two"},
        ]
    )
    selected = SelectedResource("Item", 2, "same", {}, dict(client.records[2]))
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    plan = engine.prepare(
        [Resource(kind="Item", metadata=Metadata(name="same"), spec={"description": "new"})],
        mode="patch",
        existing=[selected],
    )
    assert client.find_calls == 0
    result = engine.execute(plan)
    assert result.outcomes[0].id == 2
    assert client.records[1]["description"] == "one"
    assert client.records[2]["description"] == "new"


def test_selected_kind_change_is_rejected_before_writes() -> None:
    from untaped.capabilities.awx.application.selection import SelectedResource
    from untaped.capabilities.awx.errors import BadRequest

    spec = ResourceSpec(kind="Item", canonical_fields=("description",), identity_keys=("name",))
    client = _Client([{"id": 2, "name": "same"}])
    selected = SelectedResource("OtherKind", 2, "same", {}, dict(client.records[2]))
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    with pytest.raises(BadRequest, match="kind"):
        engine.prepare(
            [Resource(kind="Item", metadata=Metadata(name="same"), spec={})],
            mode="patch",
            existing=[selected],
        )
    assert not client.writes


def test_selected_reparenting_is_rejected_before_writes() -> None:
    from untaped.capabilities.awx.errors import BadRequest

    client = _MembershipClient([{"id": 2, "name": "group", "inventory": 99}])
    with pytest.raises(BadRequest, match="reparenting"):
        _group_engine(client).prepare(
            [_group("group", [])], mode="edit", existing=[client.records[2]]
        )
    assert not client.writes


def test_body_dependency_cycles_fail_preparation_before_unrelated_writes() -> None:
    from untaped.capabilities.awx.errors import BadRequest

    spec = ResourceSpec(
        kind="Item",
        identity_keys=("name",),
        canonical_fields=("parent",),
        fk_refs=(FkRef(field="parent", kind="Item"),),
    )
    client = _Client([])
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    docs = [
        Resource(kind="Item", metadata=Metadata(name="independent"), spec={}),
        Resource(kind="Item", metadata=Metadata(name="a"), spec={"parent": "b"}),
        Resource(kind="Item", metadata=Metadata(name="b"), spec={"parent": "a"}),
    ]
    with pytest.raises(BadRequest, match="cycle"):
        engine.prepare(docs)
    assert not client.writes


def test_new_and_existing_secret_values_are_redacted_from_runtime_errors() -> None:
    from untaped.capabilities.awx.errors import BadRequest

    class Echoing(_Client):
        def update(self, spec: ResourceSpec, id_: int, payload: Any) -> ServerRecord:
            raise BadRequest("rejected old-secret and new-secret")

    spec = ResourceSpec(
        kind="Secret",
        canonical_fields=("password",),
        identity_keys=("name",),
        secret_paths=("password",),
    )
    client = Echoing([{"id": 1, "name": "one", "password": "old-secret"}])
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    result = engine.run(
        [Resource(kind="Secret", metadata=Metadata(name="one"), spec={"password": "new-secret"})],
        write=True,
    )
    assert result.outcomes[0].action == "failed"
    assert "old-secret" not in repr(result)
    assert "new-secret" not in repr(result)


def test_failed_creation_skips_only_its_dependents_when_continuing() -> None:
    from untaped.capabilities.awx.errors import BadRequest

    class Failing(_Client):
        def create(self, spec: ResourceSpec, payload: Any) -> ServerRecord:
            if payload.name == "parent":
                raise BadRequest("failed")
            return super().create(spec, payload)

    spec = ResourceSpec(
        kind="Item",
        canonical_fields=("parent",),
        identity_keys=("name",),
        fk_refs=(FkRef(field="parent", kind="Item"),),
    )
    client = Failing([])
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    docs = [
        Resource(kind="Item", metadata=Metadata(name="parent"), spec={}),
        Resource(kind="Item", metadata=Metadata(name="child"), spec={"parent": "parent"}),
        Resource(kind="Item", metadata=Metadata(name="independent"), spec={}),
    ]
    result = engine.run(docs, write=True, continue_on_error=True)
    assert [row.action for row in result.outcomes] == ["failed", "skipped", "created"]


def test_parallel_execution_never_exceeds_http_cap() -> None:
    import threading

    barrier = threading.Barrier(10)

    class Counting(_Client):
        def __init__(self) -> None:
            super().__init__([])
            self.lock = threading.Lock()
            self.active = 0
            self.peak = 0

        def create(self, spec: ResourceSpec, payload: Any) -> ServerRecord:
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            barrier.wait(timeout=2)
            with self.lock:
                result = super().create(spec, payload)
                self.active -= 1
                return result

    spec = ResourceSpec(kind="Item", canonical_fields=(), identity_keys=("name",))
    client = Counting()
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    result = engine.execute(
        engine.prepare(
            [
                Resource(kind="Item", metadata=Metadata(name=str(index)), spec={})
                for index in range(20)
            ]
        ),
        parallel=100,
    )
    assert client.peak == 10
    assert [row.action for row in result.outcomes] == ["created"] * 20


def test_exposed_plan_values_cannot_change_the_prepared_write() -> None:
    client = _Client([{"id": 7, "name": "one", "description": "old"}])
    engine = _engine(client)
    resource = _project("one", "prepared")
    plan = engine.prepare([resource])
    resource.spec["description"] = "changed input"
    plan.operations[0].payload["description"] = "changed accessor"
    plan.operations[0].resource.spec["description"] = "changed document"
    assert engine.execute(plan).outcomes[0].action == "updated"
    assert client.records[7]["description"] == "prepared"


@pytest.mark.parametrize("drift", ["value", "preserved", "name", "enriched"])
def test_preflight_watches_unchanged_preserved_and_identity_fields_batchwide(drift: str) -> None:
    spec = ResourceSpec(
        kind="Item",
        identity_keys=("name",),
        canonical_fields=("description", "value", "password", "settings"),
        secret_paths=("password",),
        server_enriched_fields=("settings",),
    )
    records = [
        {
            "id": i,
            "name": f"item{i}",
            "description": "old",
            "value": {"keep": 1},
            "password": "old-secret",
            "settings": {"keep": 1},
        }
        for i in (1, 2)
    ]
    client = _Client(records)
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    docs = [
        Resource(
            kind="Item",
            metadata=Metadata(name=f"item{i}"),
            spec={
                "description": "new",
                "value": {"keep": 1},
                "password": "$encrypted$",
                "settings": {"keep": 1},
            },
        )
        for i in (1, 2)
    ]
    plan = engine.prepare(docs)
    field, value = {
        "value": ("value", {"other": 2}),
        "preserved": ("password", "rotated-secret"),
        "name": ("name", "renamed"),
        "enriched": ("settings", {"keep": 1, "added": 2}),
    }[drift]
    client.records[2][field] = value
    result = engine.execute(plan)
    assert [row.action for row in result.outcomes] == ["skipped", "conflict"]
    assert client.writes == []


def test_preflight_watches_nested_parent_drift_for_whole_batch() -> None:
    client = _MembershipClient(
        [
            {"id": 1, "name": "one", "inventory": 30, "description": "old"},
            {"id": 2, "name": "two", "inventory": 30, "description": "old"},
        ]
    )
    docs = [_group("one", []), _group("two", [])]
    for doc in docs:
        doc.spec["description"] = "new"
    engine = _group_engine(client)
    plan = engine.prepare(docs, mode="edit", existing=[client.records[1], client.records[2]])
    client.records[2]["inventory"] = 40
    assert [row.action for row in engine.execute(plan).outcomes] == ["skipped", "conflict"]
    assert client.writes == []


def test_distinct_selected_ids_with_duplicate_names_are_independent() -> None:
    from untaped.capabilities.awx.application.selection import SelectedResource

    spec = ResourceSpec(kind="Item", canonical_fields=("description",), identity_keys=("name",))
    client = _Client([{"id": i, "name": "same", "description": "old"} for i in (1, 2)])
    selected = [SelectedResource("Item", i, "same", {}, dict(client.records[i])) for i in (1, 2)]
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    plan = engine.prepare(
        [
            Resource(kind="Item", metadata=Metadata(name="same"), spec={"description": f"new{i}"})
            for i in (1, 2)
        ],
        mode="patch",
        existing=selected,
    )
    assert client.find_calls == 0
    result = engine.execute(plan)
    assert [(row.id, row.action) for row in result.outcomes] == [(1, "updated"), (2, "updated")]
    assert [client.records[i]["description"] for i in (1, 2)] == ["new1", "new2"]


def test_repeated_selected_id_is_rejected_before_writes() -> None:
    spec = ResourceSpec(kind="Item", canonical_fields=("description",), identity_keys=("name",))
    client = _Client([{"id": 1, "name": "same", "description": "old"}])
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({})),
        cast(StrategyResolver, _Strategies()),
    )
    docs = [
        Resource(kind="Item", metadata=Metadata(name="same"), spec={"description": value})
        for value in ("first", "second")
    ]
    with pytest.raises(MutationConflict, match="duplicate target"):
        engine.prepare(docs, mode="patch", existing=[client.records[1], client.records[1]])
    assert client.find_calls == 0
    assert client.writes == []


@pytest.mark.parametrize(
    "current,labels,expected",
    [
        ([9, 10], ["9", "other"], [9, 10]),
        ([9, 10], ["other", "9"], [10, 9]),
        ([9, 11, 10], ["other", "9", "9"], [10, 9, 11]),
    ],
)
def test_editor_preserves_membership_ids_for_ambiguous_unchanged_labels(
    current: list[int], labels: list[str], expected: list[int]
) -> None:
    class AmbiguousFk(_Fk):
        def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
            from untaped.capabilities.awx.errors import AmbiguousIdentityError

            raise AmbiguousIdentityError(kind, {"name": name}, match_count=2)

        def validate_id(self, kind: str, id_: int, *, scope: dict[str, str] | None = None) -> int:
            assert kind == "Member" and id_ in {9, 10, 11}
            return id_

    class NamedMembers(_MembershipClient):
        def paginate_sub_endpoint(
            self, spec: ResourceSpec, record_id: int, sub_endpoint: str, **kwargs: Any
        ) -> Iterator[dict[str, Any]]:
            return iter(
                {"id": item, "name": {9: "9", 10: "other", 11: "9"}[item]}
                for item in self.members.get((record_id, sub_endpoint), [])
            )

    spec = ResourceSpec(
        kind="Item",
        identity_keys=("name",),
        canonical_fields=(),
        fk_refs=(
            FkRef(field="members", kind="Member", multi=True, sub_endpoint="members", ordered=True),
        ),
    )
    client = NamedMembers([{"id": 1, "name": "item"}])
    client.members[(1, "members")] = current.copy()
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, AmbiguousFk({})),
        cast(StrategyResolver, _Strategies()),
    )
    import yaml

    from untaped.capabilities.awx.application.edit_resources import EditResources
    from untaped.capabilities.awx.application.ports import ResourceClient
    from untaped.capabilities.awx.application.save_resource import SaveResource
    from untaped.capabilities.awx.application.selection import SelectedResource

    selected = SelectedResource("Item", 1, "item", {}, client.records[1])
    batch = EditResources(
        spec,
        [selected],
        SaveResource(cast(ResourceClient, client), cast(FkResolver, AmbiguousFk({}))),
    )
    docs = list(yaml.safe_load_all(batch.render()))
    docs[0]["spec"]["members"] = labels
    resources, retained = batch.parse(yaml.safe_dump_all(docs))
    plan = engine.prepare(
        resources,
        mode="edit",
        existing=retained,
        membership_snapshots=batch.membership_snapshots,
    )
    result = engine.execute(plan)
    assert result.outcomes[0].action in {"updated", "unchanged"}
    assert client.members[(1, "members")] == expected


def test_ordered_membership_interleaves_created_reference_before_retained_tail() -> None:
    spec = ResourceSpec(
        kind="Item",
        identity_keys=("name",),
        canonical_fields=(),
        fk_refs=(
            FkRef(field="members", kind="Item", multi=True, sub_endpoint="members", ordered=True),
        ),
    )
    client = _MembershipClient(
        [{"id": 1, "name": "owner"}, {"id": 2, "name": "first"}, {"id": 3, "name": "last"}]
    )
    client.members[(1, "members")] = [2, 3]
    engine = BatchMutationEngine(
        cast(RawHttpResourceClient, client),
        cast(Catalog, _Catalog(spec)),
        cast(FkResolver, _Fk({("Item", "first"): 2, ("Item", "last"): 3})),
        cast(StrategyResolver, _Strategies()),
    )
    docs = [
        Resource(
            kind="Item", metadata=Metadata(name="owner"), spec={"members": ["first", "new", "last"]}
        ),
        Resource(kind="Item", metadata=Metadata(name="new"), spec={}),
    ]
    result = engine.run(docs, write=True)
    assert [row.action for row in result.outcomes] == ["updated", "created"]
    assert client.members[(1, "members")] == [2, 4, 3]


def test_editor_return_values_cannot_mutate_original_snapshot() -> None:
    from untaped.capabilities.awx.application.edit_resources import EditResources
    from untaped.capabilities.awx.application.ports import ResourceClient
    from untaped.capabilities.awx.application.save_resource import SaveResource
    from untaped.capabilities.awx.application.selection import SelectedResource

    spec = ResourceSpec(kind="Item", identity_keys=("name",), canonical_fields=("description",))
    record = {"id": 1, "name": "original", "description": "old"}
    batch = EditResources(
        spec,
        [SelectedResource("Item", 1, "original", {}, record)],
        SaveResource(cast(ResourceClient, _Client([record])), cast(FkResolver, _Fk({}))),
    )
    text = batch.render()
    resources, selected = batch.parse(text)
    resources[0].metadata.name = "mutated"
    selected[0].record["description"] = "mutated"
    again, original = batch.parse(text)
    assert again[0].metadata.name == "original"
    assert original[0].record["description"] == "old"
