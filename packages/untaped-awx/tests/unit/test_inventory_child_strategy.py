"""Unit tests for :class:`InventoryChildApplyStrategy`.

The strategy is the parent-scoped write path used by Host and Group:
creates POST against ``/inventories/<id>/<api_path>/`` (so ``inventory``
is implied by the URL), updates use the global ``/<api_path>/<id>/``
endpoint, and ``find_existing`` filters the nested list by name with
the same ambiguity guard as :class:`DefaultApplyStrategy`.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from untaped_awx.application.ports import FkResolver, RawHttpResourceClient
from untaped_awx.domain import IdentityRef, ResourceSpec, ServerRecord, WritePayload
from untaped_awx.errors import AmbiguousIdentityError, BadRequest
from untaped_awx.infrastructure.specs import GROUP_SPEC, HOST_SPEC
from untaped_awx.infrastructure.strategies import InventoryChildApplyStrategy


class _StubClient:
    """Minimal :class:`ResourceClient` capturing calls for assertions."""

    def __init__(
        self,
        *,
        find_results: list[dict[str, Any]] | None = None,
        find_count: int | None = None,
    ) -> None:
        self._find_results = find_results or []
        self._find_count = find_count if find_count is not None else len(self._find_results)
        self.update_calls: list[tuple[str, int, dict[str, Any]]] = []
        self.request_calls: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []

    def update(self, spec: ResourceSpec, id_: int, payload: WritePayload) -> ServerRecord:
        body = payload.model_dump()
        self.update_calls.append((spec.kind, id_, body))
        return ServerRecord(id=id_, **body)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.request_calls.append((method, path, json or {}, dict(params or {})))
        if method == "GET":
            return {"count": self._find_count, "results": list(self._find_results)}
        if method == "POST":
            return {"id": 100, **(json or {})}
        return {}


class _StubFk:
    def __init__(self, inventory_id: int = 50) -> None:
        self.inventory_id = inventory_id
        self.name_to_id_calls: list[tuple[str, str, dict[str, str] | None]] = []

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        self.name_to_id_calls.append((kind, name, scope))
        if kind != "Inventory":
            raise AssertionError(f"unexpected fk lookup for {kind!r}")
        return self.inventory_id

    def id_to_name(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    def resolve_polymorphic(self, *args: Any, **kwargs: Any) -> tuple[str, int]:
        raise NotImplementedError


def _identity(name: str = "web-01") -> dict[str, Any]:
    return {
        "name": name,
        "parent": IdentityRef(kind="Inventory", name="prod", organization="Default"),
    }


def test_create_posts_to_nested_inventory_endpoint() -> None:
    client = _StubClient()
    fk = _StubFk(inventory_id=42)
    s = InventoryChildApplyStrategy()
    s.create(
        HOST_SPEC,
        {"name": "web-01", "description": "frontend"},
        _identity(),
        client=cast(RawHttpResourceClient, client),
        fk=cast(FkResolver, fk),
    )
    method, path, body, _ = client.request_calls[-1]
    assert method == "POST"
    assert path == "inventories/42/hosts/"
    assert body["name"] == "web-01"
    assert body["description"] == "frontend"
    # Inventory FK lookup is org-scoped (the parent IdentityRef carries it).
    kind, name, scope = fk.name_to_id_calls[0]
    assert (kind, name) == ("Inventory", "prod")
    assert scope == {"organization": "Default"}


def test_create_uses_group_api_path_for_group_kind() -> None:
    client = _StubClient()
    s = InventoryChildApplyStrategy()
    s.create(
        GROUP_SPEC,
        {"name": "web", "description": "Web servers"},
        _identity(name="web"),
        client=cast(RawHttpResourceClient, client),
        fk=cast(FkResolver, _StubFk(inventory_id=42)),
    )
    _, path, _, _ = client.request_calls[-1]
    assert path == "inventories/42/groups/"


def test_find_existing_uses_nested_endpoint_with_name_filter() -> None:
    client = _StubClient(find_results=[{"id": 7, "name": "web-01"}], find_count=1)
    s = InventoryChildApplyStrategy()
    found = s.find_existing(
        HOST_SPEC,
        _identity(),
        client=cast(RawHttpResourceClient, client),
        fk=cast(FkResolver, _StubFk(inventory_id=42)),
    )
    assert found == {"id": 7, "name": "web-01"}
    method, path, _, params = client.request_calls[0]
    assert method == "GET"
    assert path == "inventories/42/hosts/"
    assert params["name"] == "web-01"
    assert params["page_size"] == "2"


def test_find_existing_returns_none_when_no_match() -> None:
    client = _StubClient(find_results=[], find_count=0)
    s = InventoryChildApplyStrategy()
    found = s.find_existing(
        HOST_SPEC,
        _identity(),
        client=cast(RawHttpResourceClient, client),
        fk=cast(FkResolver, _StubFk(inventory_id=42)),
    )
    assert found is None


def test_find_existing_raises_when_two_results() -> None:
    client = _StubClient(
        find_results=[{"id": 5, "name": "web-01"}, {"id": 6, "name": "web-01"}],
        find_count=2,
    )
    s = InventoryChildApplyStrategy()
    with pytest.raises(AmbiguousIdentityError) as excinfo:
        s.find_existing(
            HOST_SPEC,
            _identity(),
            client=cast(RawHttpResourceClient, client),
            fk=cast(FkResolver, _StubFk(inventory_id=42)),
        )
    assert excinfo.value.kind == "Host"
    assert excinfo.value.match_count == 2


def test_update_uses_global_endpoint() -> None:
    client = _StubClient()
    s = InventoryChildApplyStrategy()
    s.update(
        HOST_SPEC,
        {"id": 9, "name": "web-01"},
        {"description": "new"},
        client=cast(RawHttpResourceClient, client),
        fk=cast(FkResolver, _StubFk()),
    )
    assert client.update_calls == [("Host", 9, {"description": "new"})]


def test_create_rejects_missing_parent() -> None:
    client = _StubClient()
    s = InventoryChildApplyStrategy()
    with pytest.raises(BadRequest):
        s.create(
            HOST_SPEC,
            {"name": "x"},
            {"name": "x", "parent": None},
            client=cast(RawHttpResourceClient, client),
            fk=cast(FkResolver, _StubFk()),
        )


def test_create_rejects_non_inventory_parent_kind() -> None:
    client = _StubClient()
    s = InventoryChildApplyStrategy()
    bad_parent = IdentityRef(kind="Project", name="something")
    with pytest.raises(BadRequest):
        s.create(
            HOST_SPEC,
            {"name": "x"},
            {"name": "x", "parent": bad_parent},
            client=cast(RawHttpResourceClient, client),
            fk=cast(FkResolver, _StubFk()),
        )
