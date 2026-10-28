"""Pin the AWX ``--format raw`` first-key contract.

Without ``--columns``, ``--format raw`` prints each row's first key, so
that key is load-bearing for shell pipelines (``… --format raw | xargs``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from untaped_awx.commands.test import case_row
from untaped_awx.domain import WorkflowNode
from untaped_awx.domain.suites.suite import CaseResult, Suite
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.infrastructure.resources.specs import ALL_SPECS
from untaped_awx.records import JobEventRow, JobRecord

_SUITE = Suite.model_validate(
    {"name": "suite-a", "jobTemplate": "jt", "cases": {"c1": {"launch": {}}}}
)


@pytest.mark.parametrize(
    ("row_keys", "expected"),
    [
        (lambda: JobRecord.model_validate({"type": "job", "id": 1}).model_dump(), "id"),
        (lambda: JobEventRow.model_fields, "counter"),
        (lambda: WorkflowNode.model_fields, "id"),
        (lambda: CaseResult.model_fields, "suite"),
        (lambda: case_row(Path("s.yml"), _SUITE, "c1").model_dump(), "suite"),
    ],
    ids=["JobRecord", "JobEventRow", "WorkflowNode", "CaseResult", "case_row"],
)
def test_row_source_first_key(row_keys: Callable[[], Mapping[str, object]], expected: str) -> None:
    assert next(iter(row_keys())) == expected


def test_awx_resource_spec_list_columns_leads_with_id() -> None:
    """Every spec-driven ``list`` (catalog-only stubs included) leads with ``id``."""
    assert [spec.kind for spec in ALL_SPECS if str(spec.list_columns[0]) != "id"] == []


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda spec: spec.kind)
def test_a_catalog_row_leads_with_id_whatever_its_model_types(spec: AwxResourceSpec) -> None:
    """AWX's row starts ``id, type, …``: its record writes ``id`` first, then the typed fields."""
    row = {"type": "x", "organization": 1, "inventory": 2, "name": "n", "id": 7, "url": "/"}
    model = spec.require_record()

    assert next(iter(model.model_validate(row).model_dump())) == "id"
