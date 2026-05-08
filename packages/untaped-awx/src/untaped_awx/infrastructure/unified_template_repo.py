"""Concrete :class:`UnifiedTemplateRepository` implementation.

Wraps a :class:`RawHttpResourceClient`; the only AWX-specific piece is
the collection name ``unified_job_templates/``. Bulk ``get_by_ids`` joins
ids with ``,`` for AWX's ``id__in`` filter and orders by ``id`` so the
returned page is stable across re-runs.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from untaped_awx.application.ports import RawHttpResourceClient

_PATH = "unified_job_templates/"


class UnifiedTemplateRepository:
    def __init__(self, client: RawHttpResourceClient) -> None:
        self._client = client

    def list(
        self,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._client.paginate_path(_PATH, params=params, limit=limit)

    def get_by_ids(self, *, ids: Iterable[str]) -> Iterator[dict[str, Any]]:
        # An empty ``id__in=`` filter matches every record at AWX, so a
        # caller that bypasses ``GetUnifiedTemplate`` and hands an empty
        # list straight to the adapter would get the full collection
        # back. Materialise once (``ids`` may be a generator) and
        # short-circuit before constructing the request.
        materialised = list(ids)
        if not materialised:
            return iter(())
        return self._client.paginate_path(
            _PATH,
            params={"id__in": ",".join(materialised), "order_by": "id"},
        )
