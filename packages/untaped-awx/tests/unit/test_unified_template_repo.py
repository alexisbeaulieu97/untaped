"""Unit tests for :class:`UnifiedTemplateRepository`.

The adapter is two methods of pure delegation — list pagination through
``unified_job_templates/`` and a bulk ``?id__in=…`` lookup. Tests stub
the underlying client and assert path/params forwarding.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from untaped_awx.application.ports import RawHttpResourceClient
from untaped_awx.infrastructure.unified_template_repo import UnifiedTemplateRepository


class _FakeClient:
    def __init__(self, *, list_pages: list[dict[str, Any]] | None = None) -> None:
        self._list_pages = list(list_pages or [])
        self.paginate_calls: list[tuple[str, dict[str, str], int | None]] = []

    def paginate_path(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.paginate_calls.append((path, dict(params or {}), limit))
        return iter(self._list_pages)

    # Other RawHttpResourceClient methods unused by this adapter.
    def request(self, *a: Any, **kw: Any) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

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


def test_list_walks_unified_job_templates_endpoint() -> None:
    client = _FakeClient(list_pages=[{"id": 1, "name": "deploy"}])
    repo = UnifiedTemplateRepository(cast(RawHttpResourceClient, client))
    out = list(repo.list(params={"order_by": "name"}, limit=10))
    assert [r["id"] for r in out] == [1]
    path, params, limit = client.paginate_calls[0]
    assert path == "unified_job_templates/"
    assert params == {"order_by": "name"}
    assert limit == 10


def test_list_passes_none_params_through() -> None:
    client = _FakeClient()
    repo = UnifiedTemplateRepository(cast(RawHttpResourceClient, client))
    list(repo.list())
    _, params, limit = client.paginate_calls[0]
    assert params == {}
    assert limit is None


# ---- get_by_ids ----


def test_get_by_ids_uses_id_in_filter() -> None:
    """Bulk fetch goes through ``?id__in=1,2,3&order_by=id`` — one round
    trip rather than N. Order_by=id keeps the response stable."""
    client = _FakeClient(
        list_pages=[
            {"id": 1, "name": "deploy"},
            {"id": 2, "name": "build"},
        ]
    )
    repo = UnifiedTemplateRepository(cast(RawHttpResourceClient, client))
    out = list(repo.get_by_ids(ids=["1", "2"]))
    assert [r["id"] for r in out] == [1, 2]
    path, params, limit = client.paginate_calls[0]
    assert path == "unified_job_templates/"
    assert params == {"id__in": "1,2", "order_by": "id"}
    assert limit is None


def test_get_by_ids_empty_list_short_circuits() -> None:
    """Empty id-set must not reach AWX — ``?id__in=`` (no value) matches
    every UJT, so a caller bypassing :class:`GetUnifiedTemplate` and
    handing the adapter an empty list would otherwise receive the full
    collection. Defense in depth: the use case also short-circuits, but
    the adapter holds the line for any future caller."""
    client = _FakeClient(list_pages=[])
    repo = UnifiedTemplateRepository(cast(RawHttpResourceClient, client))
    out = list(repo.get_by_ids(ids=[]))
    assert out == []
    assert client.paginate_calls == []
