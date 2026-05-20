"""Unit tests for the ``DeleteResource`` use case."""

from __future__ import annotations

from typing import Any, cast

import pytest
from untaped_awx.application import DeleteResource
from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec, ServerRecord
from untaped_awx.errors import Conflict, ResourceNotFound
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC


class _StubClient:
    """Tracks the ``(find_by_identity | get | delete)`` calls DeleteResource needs.

    The chained ``find_by_identity → find`` shape mirrors the real
    ``ResourceRepository`` adapter so the ``(name, scope) → params``
    translation under test stays honest. ``delete_raises`` lets a test
    simulate AWX 409/404 surfacing through the client boundary.
    """

    def __init__(
        self,
        *,
        find_result: dict[str, Any] | None = None,
        get_result: dict[str, Any] | None = None,
        delete_raises: Exception | None = None,
    ) -> None:
        self._find_result = find_result
        self._get_result = get_result
        self._delete_raises = delete_raises
        self.find_calls: list[dict[str, str]] = []
        self.get_calls: list[int] = []
        self.delete_calls: list[int] = []

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        self.find_calls.append(params)
        return ServerRecord(**self._find_result) if self._find_result else None

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        params: dict[str, str] = {"name": name}
        for k, v in (scope or {}).items():
            params[f"{k}__name"] = v
        return self.find(spec, params=params)

    def get(self, spec: ResourceSpec, id_: int) -> ServerRecord:
        self.get_calls.append(id_)
        if self._get_result is None:
            raise ResourceNotFound(spec.kind, {"id": id_})
        return ServerRecord(**self._get_result)

    def delete(self, spec: ResourceSpec, id_: int) -> None:
        self.delete_calls.append(id_)
        if self._delete_raises is not None:
            raise self._delete_raises


def test_resolve_by_name_returns_record() -> None:
    client = _StubClient(find_result={"id": 42, "name": "deploy"})
    use = DeleteResource(cast(ResourceClient, client))
    record = use.resolve(JOB_TEMPLATE_SPEC, "deploy", scope={"organization": "Default"})
    assert record == {"id": 42, "name": "deploy"}
    assert client.find_calls[0] == {"name": "deploy", "organization__name": "Default"}


def test_resolve_by_numeric_id_takes_id_path() -> None:
    """All-digit identifier means id lookup — name lookup is skipped."""
    client = _StubClient(get_result={"id": 42, "name": "deploy"})
    use = DeleteResource(cast(ResourceClient, client))
    record = use.resolve(JOB_TEMPLATE_SPEC, "42")
    assert record == {"id": 42, "name": "deploy"}
    assert client.get_calls == [42]
    assert client.find_calls == []


def test_resolve_by_name_with_all_digit_escape_hatch() -> None:
    """``by_name=True`` forces the name path even for all-digit identifiers."""
    client = _StubClient(find_result={"id": 99, "name": "42"})
    use = DeleteResource(cast(ResourceClient, client))
    record = use.resolve(JOB_TEMPLATE_SPEC, "42", by_name=True)
    assert record == {"id": 99, "name": "42"}
    assert client.find_calls[0]["name"] == "42"
    assert client.get_calls == []


def test_resolve_missing_raises_not_found() -> None:
    client = _StubClient(find_result=None)
    use = DeleteResource(cast(ResourceClient, client))
    with pytest.raises(ResourceNotFound):
        use.resolve(JOB_TEMPLATE_SPEC, "ghost")


def test_delete_calls_client_with_record_id() -> None:
    client = _StubClient()
    use = DeleteResource(cast(ResourceClient, client))
    use.delete(JOB_TEMPLATE_SPEC, 42)
    assert client.delete_calls == [42]


def test_delete_propagates_conflict() -> None:
    """409 from AWX surfaces as ``Conflict`` (the CLI maps that to a stderr row)."""
    client = _StubClient(delete_raises=Conflict("resource in use"))
    use = DeleteResource(cast(ResourceClient, client))
    with pytest.raises(Conflict):
        use.delete(JOB_TEMPLATE_SPEC, 42)


def test_delete_propagates_not_found() -> None:
    """A race (deleted between resolve and delete) still surfaces typed."""
    client = _StubClient(delete_raises=ResourceNotFound("JobTemplate", {"id": 42}))
    use = DeleteResource(cast(ResourceClient, client))
    with pytest.raises(ResourceNotFound):
        use.delete(JOB_TEMPLATE_SPEC, 42)
