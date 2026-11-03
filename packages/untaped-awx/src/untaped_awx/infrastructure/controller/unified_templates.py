"""Where controller API v2 serves its polymorphic ``unified_job_templates/`` view.

Lists are alphabetical unless the caller passes its own ``order_by``
(ordering four aggregated kinds by ``-id`` would interleave four creation
timelines). A read by ids joins them with ``,`` for the ``id__in`` filter
and orders by ``id``, so the page is stable across runs.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from untaped_awx.infrastructure.controller._part import ControllerPart

_PATH = "unified_job_templates/"


class UnifiedTemplates(ControllerPart):
    """:class:`~untaped_awx.application.resources.ports.UnifiedTemplateRepository`."""

    def unified_templates(
        self,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._paginate(_PATH, params={"order_by": "name", **(params or {})}, limit=limit)

    def unified_templates_by_ids(self, *, ids: Sequence[str]) -> Iterator[dict[str, Any]]:
        # An empty ``id__in=`` filter matches every record, so no ids asks nothing.
        if not ids:
            return iter(())
        return self._paginate(_PATH, params={"id__in": ",".join(ids), "order_by": "id"})


__all__ = ["UnifiedTemplates"]
