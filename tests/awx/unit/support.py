"""Shared doubles for AWX batch mutation unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped.capabilities.awx.domain import ResourceSpec, ServerRecord, WritePayload
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


def _payload(values: dict[str, Any]) -> WritePayload:
    return WritePayload(**values)
