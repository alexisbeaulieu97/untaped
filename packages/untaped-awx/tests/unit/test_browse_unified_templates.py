"""Tests for BrowseUnifiedTemplates and GetUnifiedTemplate.

UJT is AWX's polymorphic ``/unified_job_templates/`` view aggregating
JobTemplate, WorkflowJobTemplate, Project, and InventorySource. The
browse use case applies an alphabetical default; the get use case
returns ``(records, missing_ids)`` so the CLI can report per-id misses.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, cast

from untaped_awx.application import BrowseUnifiedTemplates, GetUnifiedTemplate
from untaped_awx.application.ports import UnifiedTemplateRepository


class _FakeUjtRepo:
    def __init__(
        self,
        *,
        records: list[dict[str, Any]] | None = None,
        records_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._records = records or []
        self._by_id = records_by_id or {}
        self.list_calls: list[dict[str, Any]] = []
        self.get_by_ids_calls: list[list[str]] = []

    def list(
        self,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.list_calls.append({"params": dict(params or {}), "limit": limit})
        return iter(self._records)

    def get_by_ids(self, *, ids: Iterable[str]) -> Iterator[dict[str, Any]]:
        materialised = list(ids)
        self.get_by_ids_calls.append(materialised)
        return iter(self._by_id[i] for i in materialised if i in self._by_id)


# ---- BrowseUnifiedTemplates ----


def test_browse_applies_alphabetical_default() -> None:
    """Default ordering is ``order_by=name``; aggregating four kinds by
    ``-id`` would interleave creation timelines from four different tables."""
    repo = _FakeUjtRepo()
    list(BrowseUnifiedTemplates(cast(UnifiedTemplateRepository, repo))())
    assert repo.list_calls[0]["params"] == {"order_by": "name"}


def test_browse_preserves_caller_order_by() -> None:
    repo = _FakeUjtRepo()
    list(
        BrowseUnifiedTemplates(cast(UnifiedTemplateRepository, repo))(
            params={"order_by": "id", "type": "job_template"}
        )
    )
    assert repo.list_calls[0]["params"] == {"order_by": "id", "type": "job_template"}


def test_browse_forwards_filters_and_limit() -> None:
    repo = _FakeUjtRepo(records=[{"id": 1}, {"id": 2}])
    out = list(
        BrowseUnifiedTemplates(cast(UnifiedTemplateRepository, repo))(
            params={"name__icontains": "deploy"},
            limit=25,
        )
    )
    assert [r["id"] for r in out] == [1, 2]
    call = repo.list_calls[0]
    assert call["limit"] == 25
    assert call["params"] == {"name__icontains": "deploy", "order_by": "name"}


# ---- GetUnifiedTemplate ----


def test_get_returns_records_and_no_missing_when_all_found() -> None:
    repo = _FakeUjtRepo(
        records_by_id={
            "1": {"id": 1, "name": "deploy"},
            "2": {"id": 2, "name": "build"},
        }
    )
    records, missing = GetUnifiedTemplate(cast(UnifiedTemplateRepository, repo))(ids=["1", "2"])
    assert [r["id"] for r in records] == [1, 2]
    assert missing == []
    assert repo.get_by_ids_calls[0] == ["1", "2"]


def test_get_reports_missing_ids() -> None:
    repo = _FakeUjtRepo(records_by_id={"1": {"id": 1, "name": "deploy"}})
    records, missing = GetUnifiedTemplate(cast(UnifiedTemplateRepository, repo))(
        ids=["1", "999", "1000"]
    )
    assert [r["id"] for r in records] == [1]
    assert missing == ["999", "1000"]


def test_get_empty_ids_returns_empty_pair() -> None:
    """Empty ids must short-circuit cleanly without hitting the repo."""
    repo = _FakeUjtRepo()
    records, missing = GetUnifiedTemplate(cast(UnifiedTemplateRepository, repo))(ids=[])
    assert records == []
    assert missing == []
    assert repo.get_by_ids_calls == []


def test_get_accepts_iterable() -> None:
    repo = _FakeUjtRepo(records_by_id={"1": {"id": 1}})

    def _gen() -> Iterable[str]:
        yield "1"

    records, missing = GetUnifiedTemplate(cast(UnifiedTemplateRepository, repo))(ids=_gen())
    assert [r["id"] for r in records] == [1]
    assert missing == []


def test_get_compares_ids_as_strings() -> None:
    """AWX returns numeric ``id``; user passes strings from CLI argv. The
    use case must coerce to strings for set comparison so they match."""
    repo = _FakeUjtRepo(records_by_id={"7": {"id": 7, "name": "t"}})
    records, missing = GetUnifiedTemplate(cast(UnifiedTemplateRepository, repo))(ids=["7"])
    assert missing == []
    assert [r["id"] for r in records] == [7]


def test_get_handles_zero_padded_ids() -> None:
    """User input ``"007"`` must match AWX record ``id=7``. AWX coerces
    leading zeros in ``?id__in=007``, so the bulk fetch returns the
    record with the integer id; without canonicalisation the user's
    request would be falsely flagged as missing."""
    repo = _FakeUjtRepo(records_by_id={"007": {"id": 7, "name": "deploy"}})
    records, missing = GetUnifiedTemplate(cast(UnifiedTemplateRepository, repo))(ids=["007"])
    assert [r["id"] for r in records] == [7]
    assert missing == []
