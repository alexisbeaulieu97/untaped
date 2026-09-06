"""Unit tests for :class:`WorkflowNodeRepository` (infrastructure).

The adapter is pure delegation — the nested per-workflow endpoint and
the collection-wide reverse-lookup endpoint. Tests stub the underlying
client and assert path/params forwarding.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from untaped.capabilities.awx.application.ports import RawHttpResourceClient
from untaped.capabilities.awx.infrastructure.workflow_node_repo import WorkflowNodeRepository


class _FakeClient:
    def __init__(self, *, list_pages: list[dict[str, Any]] | None = None) -> None:
        self._list_pages = list(list_pages or [])
        self.paginate_calls: list[tuple[str, dict[str, str] | None]] = []

    def paginate_path(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.paginate_calls.append((path, dict(params) if params is not None else None))
        return iter(self._list_pages)


def test_list_nodes_walks_nested_workflow_endpoint() -> None:
    client = _FakeClient(list_pages=[{"id": 1}])
    repo = WorkflowNodeRepository(cast(RawHttpResourceClient, client))
    out = list(repo.list_nodes(workflow_id=7, params={"page_size": "200"}))
    assert [r["id"] for r in out] == [1]
    assert client.paginate_calls == [
        ("workflow_job_templates/7/workflow_nodes/", {"page_size": "200"}),
    ]


def test_list_references_filters_collection_by_unified_job_template() -> None:
    client = _FakeClient(list_pages=[{"id": 1, "workflow_job_template": 100}])
    repo = WorkflowNodeRepository(cast(RawHttpResourceClient, client))
    out = list(repo.list_references(unified_job_template=10))
    assert [r["id"] for r in out] == [1]
    assert client.paginate_calls == [
        ("workflow_job_template_nodes/", {"unified_job_template": "10"}),
    ]


def test_list_references_merges_params_without_widening_the_query() -> None:
    # A caller-supplied ``unified_job_template`` filter must not override
    # the queried template id — that would silently widen the lookup.
    client = _FakeClient()
    repo = WorkflowNodeRepository(cast(RawHttpResourceClient, client))
    list(
        repo.list_references(
            unified_job_template=10,
            params={"unified_job_template": "999", "page_size": "200"},
        )
    )
    _, params = client.paginate_calls[0]
    assert params == {"unified_job_template": "10", "page_size": "200"}
