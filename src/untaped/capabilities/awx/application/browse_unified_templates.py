"""Id-only multi-fetch over AWX's polymorphic ``/unified_job_templates/`` view.

:class:`GetUnifiedTemplate` fetches via ``?id__in=…`` and returns the records
found *and* the ids that weren't, so the CLI can emit per-miss stderr lines
and exit non-zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from untaped.capabilities.awx.application.ports import UnifiedTemplateRepository


class GetUnifiedTemplate:
    def __init__(self, repo: UnifiedTemplateRepository) -> None:
        self._repo = repo

    def __call__(self, *, ids: Iterable[str]) -> tuple[list[dict[str, Any]], list[str]]:
        wanted = list(ids)
        if not wanted:
            return [], []
        records = list(self._repo.get_by_ids(ids=wanted))
        # Normalise both sides of the comparison: AWX coerces leading
        # zeros in ``?id__in=007`` to ``7``, so the bulk fetch returns
        # ``{"id": 7}``. Without canonicalisation the user's ``"007"``
        # never matches ``"7"`` and gets falsely flagged as missing.
        found = {_canonical_id(str(r.get("id"))) for r in records}
        missing_ids = [i for i in wanted if _canonical_id(i) not in found]
        return records, missing_ids


def _canonical_id(s: str) -> str:
    return str(int(s)) if s.isdigit() else s
