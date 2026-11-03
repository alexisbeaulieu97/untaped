"""Unit tests for :class:`InventoryChildApplyStrategy`.

The strategy is the parent-scoped write path used by Host and Group:
creates go under their inventory (so ``inventory`` is implied by where
they land), updates go to the record itself, and ``find_existing``
filters the inventory's members by name with the same ambiguity guard
as :class:`DefaultApplyStrategy`. Where those live on the server is the
adapter's business, tested with the controller.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from untaped_awx.application.resources.ports import FkResolver, NestedResourceClient
from untaped_awx.domain import IdentityRef, ResourceSpec, ServerRecord, WritePayload
from untaped_awx.errors import AmbiguousIdentityError, BadRequestError
from untaped_awx.infrastructure.resources.specs import GROUP_SPEC, HOST_SPEC
from untaped_awx.infrastructure.resources.strategies import InventoryChildApplyStrategy


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
        self.nested_calls: list[tuple[str, str, int, str, dict[str, Any]]] = []

    def update(self, spec: ResourceSpec, id_: int, payload: WritePayload) -> ServerRecord:
        body = payload.model_dump()
        self.update_calls.append((spec.kind, id_, body))
        return ServerRecord(id=id_, **body)

    def named_under(
        self, spec: ResourceSpec, parent_kind: str, parent_id: int, *, name: str
    ) -> tuple[list[dict[str, Any]], int | None]:
        self.nested_calls.append(("find", parent_kind, parent_id, spec.kind, {"name": name}))
        return list(self._find_results), self._find_count

    def create_under(
        self, spec: ResourceSpec, parent_kind: str, parent_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        self.nested_calls.append(("create", parent_kind, parent_id, spec.kind, body))
        return {"id": 100, **body}


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
        {
            **_identity(),
            "_prepared_parent": s.prepare_parent(HOST_SPEC, _identity(), fk=cast(FkResolver, fk)),
        },
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, fk),
    )
    action, parent_kind, parent_id, kind, body = client.nested_calls[-1]
    assert (action, parent_kind, parent_id, kind) == ("create", "Inventory", 42, "Host")
    assert body["name"] == "web-01"
    assert body["description"] == "frontend"
    # Inventory FK lookup is org-scoped (the parent IdentityRef carries it).
    kind, name, scope = fk.name_to_id_calls[0]
    assert (kind, name) == ("Inventory", "prod")
    assert scope == {"organization": "Default"}


def test_create_puts_a_group_under_its_inventory() -> None:
    client = _StubClient()
    s = InventoryChildApplyStrategy()
    s.create(
        GROUP_SPEC,
        {"name": "web", "description": "Web servers"},
        {**_identity(name="web"), "_prepared_parent": ("Inventory", 42)},
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, _StubFk(inventory_id=42)),
    )
    assert client.nested_calls[-1][:4] == ("create", "Inventory", 42, "Group")


def test_find_existing_looks_by_name_under_the_inventory() -> None:
    client = _StubClient(find_results=[{"id": 7, "name": "web-01"}], find_count=1)
    s = InventoryChildApplyStrategy()
    found = s.find_existing(
        HOST_SPEC,
        _identity(),
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, _StubFk(inventory_id=42)),
    )
    assert found == {"id": 7, "name": "web-01"}
    assert client.nested_calls == [("find", "Inventory", 42, "Host", {"name": "web-01"})]


def test_find_existing_returns_none_when_no_match() -> None:
    client = _StubClient(find_results=[], find_count=0)
    s = InventoryChildApplyStrategy()
    found = s.find_existing(
        HOST_SPEC,
        _identity(),
        client=cast(NestedResourceClient, client),
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
            client=cast(NestedResourceClient, client),
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
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, _StubFk()),
    )
    assert client.update_calls == [("Host", 9, {"description": "new"})]


def test_create_rejects_missing_parent() -> None:
    s = InventoryChildApplyStrategy()
    with pytest.raises(BadRequestError):
        s.prepare_parent(
            HOST_SPEC,
            {"name": "x", "parent": None},
            fk=cast(FkResolver, _StubFk()),
        )


def test_create_rejects_non_inventory_parent_kind() -> None:
    s = InventoryChildApplyStrategy()
    bad_parent = IdentityRef(kind="Project", name="something")
    with pytest.raises(BadRequestError):
        s.prepare_parent(
            HOST_SPEC,
            {"name": "x", "parent": bad_parent},
            fk=cast(FkResolver, _StubFk()),
        )
