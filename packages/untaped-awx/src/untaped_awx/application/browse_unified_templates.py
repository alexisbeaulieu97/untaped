"""Read use cases for AWX's polymorphic ``/unified_job_templates/`` view.

Two use cases share the file because they share a port and neither is
big enough to warrant its own module:

- :class:`BrowseUnifiedTemplates` — paginated list, alphabetical default.
- :class:`GetUnifiedTemplate` — id-only multi-fetch via ``?id__in=…``;
  returns the records found *and* the ids that weren't, so the CLI can
  emit per-miss stderr lines and exit non-zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from untaped_awx.application.ports import UnifiedTemplateRepository


class BrowseUnifiedTemplates:
    def __init__(self, repo: UnifiedTemplateRepository) -> None:
        self._repo = repo

    def __call__(
        self,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        merged = dict(params or {})
        merged.setdefault("order_by", "name")
        return self._repo.list(params=merged, limit=limit)


class GetUnifiedTemplate:
    def __init__(self, repo: UnifiedTemplateRepository) -> None:
        self._repo = repo

    def __call__(self, *, ids: Iterable[str]) -> tuple[list[dict[str, Any]], list[str]]:
        wanted = list(ids)
        if not wanted:
            return [], []
        records = list(self._repo.get_by_ids(ids=wanted))
        found = {str(r.get("id")) for r in records}
        missing_ids = [i for i in wanted if i not in found]
        return records, missing_ids
