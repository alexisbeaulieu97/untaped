"""Unit tests for :class:`JobRecordRepository`.

The adapter wraps a :class:`RawHttpResourceClient` and adds one piece of
knowledge: which AWX collection corresponds to which ``Job.kind``. Tests
stub the client and assert path / params forwarding — pagination
itself is exercised by ``test_resource_repo.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from untaped_awx.application.ports import RawHttpResourceClient
from untaped_awx.infrastructure.job_record_repo import JobRecordRepository


class _FakeClient:
    def __init__(
        self,
        *,
        list_pages: list[dict[str, Any]] | None = None,
        get_response: dict[str, Any] | None = None,
    ) -> None:
        self._list_pages = list(list_pages or [])
        self._get_response = get_response or {}
        self.paginate_calls: list[tuple[str, dict[str, str], int | None]] = []
        self.request_calls: list[tuple[str, str, dict[str, str]]] = []

    def paginate_path(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.paginate_calls.append((path, dict(params or {}), limit))
        return iter(self._list_pages)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.request_calls.append((method, path, dict(params or {})))
        return dict(self._get_response)

    # Methods below are part of RawHttpResourceClient but unused by the
    # repository under test; left as no-ops to satisfy the Protocol.
    def request_text(self, *a: Any, **kw: Any) -> str:  # pragma: no cover
        return ""

    def list(self, *a: Any, **kw: Any) -> Iterator[dict[str, Any]]:  # pragma: no cover
        return iter(())

    def get(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
        return None

    def find(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
        return None

    def find_by_identity(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
        return None

    def create(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def update(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def delete(self, *a: Any, **kw: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    def action(self, *a: Any, **kw: Any) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def sub_endpoint_request(self, *a: Any, **kw: Any) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def paginate_sub_endpoint(
        self, *a: Any, **kw: Any
    ) -> Iterator[dict[str, Any]]:  # pragma: no cover
        return iter(())


# ---- list ----


def test_list_jobs_uses_jobs_collection() -> None:
    client = _FakeClient(list_pages=[{"id": 1}])
    repo = JobRecordRepository(cast(RawHttpResourceClient, client))
    out = list(repo.list(kind="job", params={"order_by": "-id"}, limit=20))
    assert [r["id"] for r in out] == [1]
    path, params, limit = client.paginate_calls[0]
    assert path == "jobs/"
    assert params == {"order_by": "-id"}
    assert limit == 20


def test_list_workflow_jobs_uses_workflow_jobs_collection() -> None:
    client = _FakeClient()
    repo = JobRecordRepository(cast(RawHttpResourceClient, client))
    list(repo.list(kind="workflow_job"))
    assert client.paginate_calls[0][0] == "workflow_jobs/"


def test_list_each_kind_in_map_routes_correctly() -> None:
    """Every kind in ``KIND_TO_API_PATH`` must map to its dedicated AWX
    collection; a regression here would silently route polls to the
    wrong endpoint."""
    expected = {
        "job": "jobs/",
        "workflow_job": "workflow_jobs/",
        "project_update": "project_updates/",
        "inventory_update": "inventory_updates/",
        "ad_hoc_command": "ad_hoc_commands/",
    }
    for kind, path in expected.items():
        client = _FakeClient()
        repo = JobRecordRepository(cast(RawHttpResourceClient, client))
        list(repo.list(kind=kind))
        assert client.paginate_calls[0][0] == path, kind


def test_list_unknown_kind_falls_through_unchanged() -> None:
    """Kinds outside the map use the raw value as the collection — same
    as the prior ``_kind_path(kind)`` helper. Defensive but rarely hit."""
    client = _FakeClient()
    repo = JobRecordRepository(cast(RawHttpResourceClient, client))
    list(repo.list(kind="custom_kind"))
    assert client.paginate_calls[0][0] == "custom_kind/"


def test_list_passes_none_params_through() -> None:
    client = _FakeClient()
    repo = JobRecordRepository(cast(RawHttpResourceClient, client))
    list(repo.list(kind="job"))
    _, params, limit = client.paginate_calls[0]
    assert params == {}
    assert limit is None


# ---- get ----


def test_get_job_hits_jobs_id_endpoint() -> None:
    client = _FakeClient(get_response={"id": 42, "status": "successful"})
    repo = JobRecordRepository(cast(RawHttpResourceClient, client))
    record = repo.get(kind="job", job_id=42)
    assert record == {"id": 42, "status": "successful"}
    method, path, params = client.request_calls[0]
    assert method == "GET"
    assert path == "jobs/42/"
    assert params == {}


def test_get_workflow_job_hits_workflow_jobs_id_endpoint() -> None:
    client = _FakeClient(get_response={"id": 9, "status": "running"})
    repo = JobRecordRepository(cast(RawHttpResourceClient, client))
    repo.get(kind="workflow_job", job_id=9)
    assert client.request_calls[0][1] == "workflow_jobs/9/"
