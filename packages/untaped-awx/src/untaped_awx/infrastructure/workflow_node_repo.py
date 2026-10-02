"""Concrete :class:`HttpWorkflowNodeRepository` over AWX's workflow-nodes endpoints.

Wraps a :class:`RawHttpResourceClient`; the only AWX-specific pieces are
the URL shapes ``workflow_job_templates/<id>/workflow_nodes/`` (the
nodes of one workflow, and where a node is created),
``workflow_job_template_nodes/`` (the collection-wide view, filterable by
``unified_job_template`` for reverse lookups; ``<id>/`` updates or deletes a
node, ``<id>/<relation>/`` holds its edges and prompt memberships) and
``workflow_approval_templates/<id>/`` (an approval node's template).
Pagination goes through :meth:`paginate_path` so result sets with more
than one page (the default ``page_size`` is 200) don't silently truncate.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from untaped_awx.application.ports import RawHttpResourceClient


class HttpWorkflowNodeRepository:
    def __init__(self, client: RawHttpResourceClient) -> None:
        self._client = client

    def list_nodes(
        self,
        *,
        workflow_id: int,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._client.paginate_path(
            f"workflow_job_templates/{workflow_id}/workflow_nodes/",
            params=params,
        )

    def list_references(
        self,
        *,
        unified_job_template: int,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        # Our key goes last so a caller-supplied ``unified_job_template``
        # filter cannot silently widen the query.
        merged = {**(params or {}), "unified_job_template": str(unified_job_template)}
        return self._client.paginate_path(
            f"{_NODES}/",
            params=merged,
        )

    def list_node_members(self, *, node_id: int, relation: str) -> Iterator[dict[str, Any]]:
        return self._client.paginate_path(f"{_NODES}/{node_id}/{relation}/")

    def create_node(self, *, workflow_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self._client.request(
            "POST", f"workflow_job_templates/{workflow_id}/workflow_nodes/", json=body
        )

    def update_node(self, *, node_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self._client.request("PATCH", f"{_NODES}/{node_id}/", json=body)

    def delete_node(self, *, node_id: int) -> None:
        self._client.request("DELETE", f"{_NODES}/{node_id}/")

    def link_node(
        self, *, node_id: int, relation: str, member_id: int, disassociate: bool = False
    ) -> None:
        body: dict[str, Any] = {"id": member_id}
        if disassociate:
            body["disassociate"] = True
        self._client.request("POST", f"{_NODES}/{node_id}/{relation}/", json=body)

    def get_approval_template(self, *, template_id: int) -> dict[str, Any]:
        return self._client.request("GET", f"{_APPROVALS}/{template_id}/")

    def create_approval_template(self, *, node_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self._client.request(
            "POST", f"{_NODES}/{node_id}/create_approval_template/", json=body
        )

    def update_approval_template(self, *, template_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self._client.request("PATCH", f"{_APPROVALS}/{template_id}/", json=body)


_NODES = "workflow_job_template_nodes"
_APPROVALS = "workflow_approval_templates"
