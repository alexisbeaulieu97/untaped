from __future__ import annotations

from typing import Any

import pytest
from untaped_awx.domain import IdentityRef, ResourceSpec
from untaped_awx.errors import AmbiguousIdentityError, BadRequest
from untaped_awx.infrastructure.specs import (
    JOB_TEMPLATE_SPEC,
    SCHEDULE_SPEC,
)
from untaped_awx.infrastructure.strategies import (
    DefaultApplyStrategy,
    ScheduleApplyStrategy,
)


class _StubClient:
    def __init__(self, find_result: dict[str, Any] | None = None) -> None:
        self.find_result = find_result
        self.find_calls: list[tuple[str, dict[str, str]]] = []
        self.create_calls: list[tuple[str, dict[str, Any]]] = []
        self.update_calls: list[tuple[str, int, dict[str, Any]]] = []
        self.request_calls: list[tuple[str, str, dict[str, Any]]] = []

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> dict[str, Any] | None:
        self.find_calls.append((spec.kind, params))
        return self.find_result

    def create(self, spec: ResourceSpec, payload: dict[str, Any]) -> dict[str, Any]:
        self.create_calls.append((spec.kind, payload))
        return {"id": 100, **payload}

    def update(self, spec: ResourceSpec, id_: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.update_calls.append((spec.kind, id_, payload))
        return {"id": id_, **payload}

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.request_calls.append((method, path, json or {}))
        if method == "GET":
            return {"results": [self.find_result] if self.find_result else []}
        return {"id": 200, "name": (json or {}).get("name", "")}


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
        client=client,  # type: ignore[arg-type]
        fk=_StubFk(),  # type: ignore[arg-type]
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
        client=client,  # type: ignore[arg-type]
        fk=_StubFk(),  # type: ignore[arg-type]
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
        client=client,  # type: ignore[arg-type]
        fk=_StubFk(),  # type: ignore[arg-type]
    )
    assert client.update_calls[0] == ("JobTemplate", 99, {"description": "updated"})


def test_schedule_strategy_create_uses_parent_endpoint() -> None:
    client = _StubClient()
    fk = _StubFk(parent_id=42)
    s = ScheduleApplyStrategy()
    parent = IdentityRef(kind="JobTemplate", name="deploy", organization="Default")
    s.create(
        SCHEDULE_SPEC,
        {"rrule": "FREQ=DAILY"},
        {"name": "nightly", "parent": parent},
        client=client,  # type: ignore[arg-type]
        fk=fk,  # type: ignore[arg-type]
    )
    method, path, json = client.request_calls[-1]
    assert method == "POST"
    assert path == "job_templates/42/schedules/"
    assert json["name"] == "nightly"
    assert json["rrule"] == "FREQ=DAILY"


def test_schedule_strategy_update_uses_global_endpoint() -> None:
    client = _StubClient()
    s = ScheduleApplyStrategy()
    s.update(
        SCHEDULE_SPEC,
        {"id": 7, "name": "nightly"},
        {"rrule": "FREQ=WEEKLY"},
        client=client,  # type: ignore[arg-type]
        fk=_StubFk(),  # type: ignore[arg-type]
    )
    assert client.update_calls[0] == ("Schedule", 7, {"rrule": "FREQ=WEEKLY"})


def test_schedule_strategy_find_uses_parent_endpoint() -> None:
    client = _StubClient(find_result={"id": 5, "name": "nightly"})
    s = ScheduleApplyStrategy()
    parent = IdentityRef(kind="JobTemplate", name="deploy", organization="Default")
    found = s.find_existing(
        SCHEDULE_SPEC,
        {"name": "nightly", "parent": parent},
        client=client,  # type: ignore[arg-type]
        fk=_StubFk(parent_id=42),  # type: ignore[arg-type]
    )
    assert found == {"id": 5, "name": "nightly"}
    method, path, _ = client.request_calls[0]
    assert method == "GET"
    assert path == "job_templates/42/schedules/"


def test_schedule_strategy_rejects_unknown_parent_kind() -> None:
    client = _StubClient()
    s = ScheduleApplyStrategy()
    parent = IdentityRef(kind="UnknownThing", name="x")
    with pytest.raises(BadRequest):
        s.create(
            SCHEDULE_SPEC,
            {},
            {"name": "x", "parent": parent},
            client=client,  # type: ignore[arg-type]
            fk=_StubFk(),  # type: ignore[arg-type]
        )


class _MultiResultClient:
    """Stub that returns N results from `request` GET — for ambiguity tests."""

    def __init__(self, results: list[dict[str, Any]], count: int | None = None) -> None:
        self._results = results
        self._count = count if count is not None else len(results)
        self.request_calls: list[tuple[str, str, dict[str, str]]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.request_calls.append((method, path, dict(params or {})))
        return {"count": self._count, "results": self._results}

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
            client=client,  # type: ignore[arg-type]
            fk=_StubFk(parent_id=42),  # type: ignore[arg-type]
        )
    assert excinfo.value.kind == "Schedule"
    assert excinfo.value.match_count == 2
    # The parent is materialized as kind#id in the identity payload so
    # the message is unambiguous about *which* parent the lookup hit.
    assert "name" in excinfo.value.identity
    method, path, params = client.request_calls[0]
    assert method == "GET"
    assert path == "job_templates/42/schedules/"
    assert params["page_size"] == "2"


def test_schedule_strategy_rejects_missing_parent() -> None:
    client = _StubClient()
    s = ScheduleApplyStrategy()
    with pytest.raises(BadRequest):
        s.create(
            SCHEDULE_SPEC,
            {},
            {"name": "x", "parent": None},
            client=client,  # type: ignore[arg-type]
            fk=_StubFk(),  # type: ignore[arg-type]
        )
