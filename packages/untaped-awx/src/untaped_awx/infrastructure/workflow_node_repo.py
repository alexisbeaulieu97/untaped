"""Concrete :class:`WorkflowNodeRepository` over AWX's nested workflow-nodes endpoint.

Wraps a :class:`RawHttpResourceClient`; the only AWX-specific piece is
the URL shape ``workflow_job_templates/<id>/workflow_nodes/``. Pagination
goes through :meth:`paginate_path` so workflows with more than one page
of nodes (the default ``page_size`` is 200) don't silently truncate.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from untaped_awx.application.ports import RawHttpResourceClient


class WorkflowNodeRepository:
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
