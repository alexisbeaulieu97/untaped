"""Concrete :class:`WorkflowNodeRepository` over AWX's workflow-nodes endpoints.

Wraps a :class:`RawHttpResourceClient`; the only AWX-specific pieces are
the URL shapes ``workflow_job_templates/<id>/workflow_nodes/`` (the
nodes of one workflow) and ``workflow_job_template_nodes/`` (the
collection-wide view, filterable by ``unified_job_template`` for reverse
lookups). Pagination goes through :meth:`paginate_path` so result sets
with more than one page (the default ``page_size`` is 200) don't
silently truncate.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from untaped.capabilities.awx.application.ports import RawHttpResourceClient


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
            "workflow_job_template_nodes/",
            params=merged,
        )
