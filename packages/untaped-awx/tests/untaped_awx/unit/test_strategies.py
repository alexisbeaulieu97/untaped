from __future__ import annotations

from typing import Any, cast

import pytest

from untaped_awx.application.resources.ports import FkResolver, NestedResourceClient
from untaped_awx.domain import IdentityRef, ResourceSpec, ServerRecord, WritePayload
from untaped_awx.errors import AmbiguousIdentityError, BadRequestError
from untaped_awx.infrastructure.resources.specs import (
    JOB_TEMPLATE_SPEC,
    SCHEDULE_SPEC,
)
from untaped_awx.infrastructure.resources.strategies import (
    DefaultApplyStrategy,
    ScheduleApplyStrategy,
)


class _StubClient:
    def __init__(self, find_result: dict[str, Any] | None = None) -> None:
        self.find_result = find_result
        self.find_calls: list[tuple[str, dict[str, str]]] = []
        self.create_calls: list[tuple[str, dict[str, Any]]] = []
        self.update_calls: list[tuple[str, int, dict[str, Any]]] = []
        self.nested_calls: list[tuple[str, str, int, str, dict[str, Any]]] = []

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        self.find_calls.append((spec.kind, params))
        return ServerRecord(**self.find_result) if self.find_result else None

    def create(self, spec: ResourceSpec, payload: WritePayload) -> ServerRecord:
        body = payload.model_dump()
        self.create_calls.append((spec.kind, body))
        return ServerRecord(id=100, **body)

    def update(self, spec: ResourceSpec, id_: int, payload: WritePayload) -> ServerRecord:
        body = payload.model_dump()
        self.update_calls.append((spec.kind, id_, body))
        return ServerRecord(id=id_, **body)

    def named_under(
        self, spec: ResourceSpec, parent_kind: str, parent_id: int, *, name: str
    ) -> tuple[list[dict[str, Any]], int | None]:
        self.nested_calls.append(("find", parent_kind, parent_id, spec.kind, {"name": name}))
        found = [self.find_result] if self.find_result else []
        return found, len(found)

    def create_under(
        self, spec: ResourceSpec, parent_kind: str, parent_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        self.nested_calls.append(("create", parent_kind, parent_id, spec.kind, body))
        return {"id": 200, "name": body.get("name", "")}


class _StubFk:
    """Resolves Schedule's parent value to (kind, id) without an API call."""

    def __init__(self, parent_id: int = 42) -> None:
        self.parent_id = parent_id
        self.calls: list[dict[str, Any]] = []

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        self.calls.append(value)
        return value["kind"], self.parent_id

    def name_to_id(self, *args: Any, **kwargs: Any) -> int:
        raise NotImplementedError

    def id_to_name(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError


def test_default_strategy_uses_scope_field_name_lookup() -> None:
    client = _StubClient(find_result={"id": 7, "name": "deploy"})
    s = DefaultApplyStrategy()
    s.find_existing(
        JOB_TEMPLATE_SPEC,
        {"name": "deploy", "organization": "Default"},
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, _StubFk()),
    )
    assert client.find_calls[0][1] == {
        "name": "deploy",
        "organization__name": "Default",
    }


def test_default_strategy_create_calls_client_create() -> None:
    client = _StubClient()
    s = DefaultApplyStrategy()
    result = s.create(
        JOB_TEMPLATE_SPEC,
        {"name": "deploy", "playbook": "x.yml"},
        {"name": "deploy"},
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, _StubFk()),
    )
    assert result["id"] == 100
    assert client.create_calls[0][0] == "JobTemplate"


def test_default_strategy_update_uses_existing_id() -> None:
    client = _StubClient()
    s = DefaultApplyStrategy()
    s.update(
        JOB_TEMPLATE_SPEC,
        {"id": 99, "name": "deploy"},
        {"description": "updated"},
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, _StubFk()),
    )
    assert client.update_calls[0] == ("JobTemplate", 99, {"description": "updated"})


def test_schedule_strategy_creates_under_its_parent() -> None:
    client = _StubClient()
    fk = _StubFk(parent_id=42)
    s = ScheduleApplyStrategy()
    parent = IdentityRef(kind="JobTemplate", name="deploy", organization="Default")
    s.create(
        SCHEDULE_SPEC,
        {"rrule": "FREQ=DAILY"},
        {
            "name": "nightly",
            "parent": parent,
            "_prepared_parent": s.prepare_parent(
                SCHEDULE_SPEC, {"parent": parent}, fk=cast(FkResolver, fk)
            ),
        },
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, fk),
    )
    action, parent_kind, parent_id, kind, json = client.nested_calls[-1]
    assert (action, parent_kind, parent_id, kind) == ("create", "JobTemplate", 42, "Schedule")
    assert json["name"] == "nightly"
    assert json["rrule"] == "FREQ=DAILY"


def test_schedule_strategy_update_uses_global_endpoint() -> None:
    client = _StubClient()
    s = ScheduleApplyStrategy()
    s.update(
        SCHEDULE_SPEC,
        {"id": 7, "name": "nightly"},
        {"rrule": "FREQ=WEEKLY"},
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, _StubFk()),
    )
    assert client.update_calls[0] == ("Schedule", 7, {"rrule": "FREQ=WEEKLY"})


def test_schedule_strategy_finds_under_its_parent() -> None:
    client = _StubClient(find_result={"id": 5, "name": "nightly"})
    s = ScheduleApplyStrategy()
    parent = IdentityRef(kind="JobTemplate", name="deploy", organization="Default")
    found = s.find_existing(
        SCHEDULE_SPEC,
        {"name": "nightly", "parent": parent},
        client=cast(NestedResourceClient, client),
        fk=cast(FkResolver, _StubFk(parent_id=42)),
    )
    assert found == {"id": 5, "name": "nightly"}
    assert client.nested_calls == [("find", "JobTemplate", 42, "Schedule", {"name": "nightly"})]


def test_schedule_strategy_rejects_unknown_parent_kind() -> None:
    s = ScheduleApplyStrategy()
    parent = IdentityRef(kind="UnknownThing", name="x")
    with pytest.raises(BadRequestError):
        s.prepare_parent(
            SCHEDULE_SPEC, {"name": "x", "parent": parent}, fk=cast(FkResolver, _StubFk())
        )


class _MultiResultClient:
    """Stub whose by-name lookup answers N results — for ambiguity tests."""

    def __init__(self, results: list[dict[str, Any]], count: int | None = None) -> None:
        self._results = results
        self._count = count if count is not None else len(results)
        self.lookups: list[tuple[str, int, str, str]] = []

    def named_under(
        self, spec: ResourceSpec, parent_kind: str, parent_id: int, *, name: str
    ) -> tuple[list[dict[str, Any]], int | None]:
        self.lookups.append((parent_kind, parent_id, spec.kind, name))
        return self._results, self._count

    def find(self, *a: Any, **kw: Any) -> dict[str, Any] | None:
        raise NotImplementedError

    def create(self, *a: Any, **kw: Any) -> dict[str, Any]:
        raise NotImplementedError

    def update(self, *a: Any, **kw: Any) -> dict[str, Any]:
        raise NotImplementedError


def test_schedule_strategy_raises_on_ambiguous_lookup() -> None:
    """Two schedules sharing a name under one parent must raise rather
    than silently picking whichever AWX returned first."""
    client = _MultiResultClient(
        results=[
            {"id": 5, "name": "nightly"},
            {"id": 6, "name": "nightly"},
        ],
        count=2,
    )
    s = ScheduleApplyStrategy()
    parent = IdentityRef(kind="JobTemplate", name="deploy", organization="Default")
    with pytest.raises(AmbiguousIdentityError) as excinfo:
        s.find_existing(
            SCHEDULE_SPEC,
            {"name": "nightly", "parent": parent},
            client=cast(NestedResourceClient, client),
            fk=cast(FkResolver, _StubFk(parent_id=42)),
        )
    assert excinfo.value.kind == "Schedule"
    assert excinfo.value.match_count == 2
    # The parent is materialized as kind#id in the identity payload so
    # the message is unambiguous about *which* parent the lookup hit.
    assert "name" in excinfo.value.identity
    assert client.lookups == [("JobTemplate", 42, "Schedule", "nightly")]


def test_schedule_strategy_rejects_missing_parent() -> None:
    client = _StubClient()
    s = ScheduleApplyStrategy()
    with pytest.raises(BadRequestError):
        s.create(
            SCHEDULE_SPEC,
            {},
            {"name": "x", "parent": None},
            client=cast(NestedResourceClient, client),
            fk=cast(FkResolver, _StubFk()),
        )
