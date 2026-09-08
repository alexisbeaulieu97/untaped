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
from untaped.capabilities.awx.infrastructure.specs import PROJECT_SPEC


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

    def name_to_id(
        self, kind: str, name: str, *, scope: dict[str, str] | None = None
    ) -> int:
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

    def delete(self, spec: ResourceSpec, id_: int) -> None:
        del self.records[id_]

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


class _Strategy:
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
