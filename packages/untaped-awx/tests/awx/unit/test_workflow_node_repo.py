"""Unit tests for :class:`HttpWorkflowNodeRepository` (infrastructure).

The adapter is pure delegation — the nested per-workflow endpoint and
the collection-wide reverse-lookup endpoint. Tests stub the underlying
client and assert path/params forwarding.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from untaped_awx.application.ports import RawHttpResourceClient
from untaped_awx.infrastructure.workflow_node_repo import HttpWorkflowNodeRepository


class _FakeClient:
    def __init__(self, *, list_pages: list[dict[str, Any]] | None = None) -> None:
        self._list_pages = list(list_pages or [])
        self.paginate_calls: list[tuple[str, dict[str, str] | None]] = []
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.requests.append((method, path, json))
        return {"id": 5}

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
    repo = HttpWorkflowNodeRepository(cast(RawHttpResourceClient, client))
    out = list(repo.list_nodes(workflow_id=7, params={"page_size": "200"}))
    assert [r["id"] for r in out] == [1]
    assert client.paginate_calls == [
        ("workflow_job_templates/7/workflow_nodes/", {"page_size": "200"}),
    ]


def test_list_references_filters_collection_by_unified_job_template() -> None:
    client = _FakeClient(list_pages=[{"id": 1, "workflow_job_template": 100}])
    repo = HttpWorkflowNodeRepository(cast(RawHttpResourceClient, client))
    out = list(repo.list_references(unified_job_template=10))
    assert [r["id"] for r in out] == [1]
    assert client.paginate_calls == [
        ("workflow_job_template_nodes/", {"unified_job_template": "10"}),
    ]


def test_list_references_merges_params_without_widening_the_query() -> None:
    # A caller-supplied ``unified_job_template`` filter must not override
    # the queried template id — that would silently widen the lookup.
    client = _FakeClient()
    repo = HttpWorkflowNodeRepository(cast(RawHttpResourceClient, client))
    list(
        repo.list_references(
            unified_job_template=10,
            params={"unified_job_template": "999", "page_size": "200"},
        )
    )
    _, params = client.paginate_calls[0]
    assert params == {"unified_job_template": "10", "page_size": "200"}


def test_node_writes_use_the_node_and_approval_endpoints() -> None:
    client = _FakeClient(list_pages=[{"id": 3}])
    repo = HttpWorkflowNodeRepository(cast(RawHttpResourceClient, client))

    assert repo.create_node(workflow_id=7, body={"identifier": "a"}) == {"id": 5}
    repo.update_node(node_id=5, body={"limit": "web"})
    repo.link_node(node_id=5, relation="success_nodes", member_id=6)
    repo.link_node(node_id=5, relation="credentials", member_id=9, disassociate=True)
    repo.create_approval_template(node_id=5, body={"name": "ok"})
    repo.update_approval_template(template_id=8, body={"timeout": 60})
    repo.get_approval_template(template_id=8)
    repo.delete_node(node_id=5)
    assert [r["id"] for r in repo.list_node_members(node_id=5, relation="labels")] == [3]

    assert client.requests == [
        ("POST", "workflow_job_templates/7/workflow_nodes/", {"identifier": "a"}),
        ("PATCH", "workflow_job_template_nodes/5/", {"limit": "web"}),
        ("POST", "workflow_job_template_nodes/5/success_nodes/", {"id": 6}),
        (
            "POST",
            "workflow_job_template_nodes/5/credentials/",
            {"id": 9, "disassociate": True},
        ),
        ("POST", "workflow_job_template_nodes/5/create_approval_template/", {"name": "ok"}),
        ("PATCH", "workflow_approval_templates/8/", {"timeout": 60}),
        ("GET", "workflow_approval_templates/8/", None),
        ("DELETE", "workflow_job_template_nodes/5/", None),
    ]
    assert client.paginate_calls == [("workflow_job_template_nodes/5/labels/", None)]
