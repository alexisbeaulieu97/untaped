"""Where controller API v2 serves workflow-template nodes.

``workflow_job_templates/<id>/workflow_nodes/`` holds one workflow's nodes
(and is where a node is created); ``workflow_job_template_nodes/`` is the
collection-wide view, filterable by ``unified_job_template`` for reverse
lookups, where ``<id>/`` updates or deletes a node and ``<id>/<relation>/``
holds its edges and prompt memberships; ``workflow_approval_templates/<id>/``
is an approval node's template.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped_awx.infrastructure.controller._part import ControllerPart

_NODES = "workflow_job_template_nodes"
_APPROVALS = "workflow_approval_templates"


def _workflow_nodes(workflow_id: int) -> str:
    return f"workflow_job_templates/{workflow_id}/workflow_nodes/"


class WorkflowNodes(ControllerPart):
    """:class:`~untaped_awx.application.resources.ports.WorkflowNodeRepository`."""

    def list_nodes(
        self,
        *,
        workflow_id: int,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._paginate(
            f"workflow_job_templates/{workflow_id}/workflow_nodes/", params=params
        )

    def list_references(
        self,
        *,
        unified_job_template: int,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        # Our key goes last so a caller's ``unified_job_template`` filter cannot widen the query.
        merged = {**(params or {}), "unified_job_template": str(unified_job_template)}
        return self._paginate(f"{_NODES}/", params=merged)

    def list_node_members(self, *, node_id: int, relation: str) -> Iterator[dict[str, Any]]:
        return self._paginate(f"{_NODES}/{node_id}/{relation}/")

    def create_node(self, *, workflow_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", _workflow_nodes(workflow_id), json=body)

    def update_node(self, *, node_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"{_NODES}/{node_id}/", json=body)

    def delete_node(self, *, node_id: int) -> None:
        self._request("DELETE", f"{_NODES}/{node_id}/")

    def link_node(
        self, *, node_id: int, relation: str, member_id: int, disassociate: bool = False
    ) -> None:
        body: dict[str, Any] = {"id": member_id}
        if disassociate:
            body["disassociate"] = True
        self._request("POST", f"{_NODES}/{node_id}/{relation}/", json=body)

    def get_approval_template(self, *, template_id: int) -> dict[str, Any]:
        return self._request("GET", f"{_APPROVALS}/{template_id}/")

    def create_approval_template(self, *, node_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{_NODES}/{node_id}/create_approval_template/", json=body)

    def update_approval_template(self, *, template_id: int, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"{_APPROVALS}/{template_id}/", json=body)


__all__ = ["WorkflowNodes"]
