"""Pin the AWX ``--format raw`` first-key contract.

Without ``--columns``, ``--format raw`` prints each row's first key, so
that key is load-bearing for shell pipelines (``… --format raw | xargs``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from untaped_awx.cli.suite_commands import case_row
from untaped_awx.domain import Job, JobEvent, WorkflowNode
from untaped_awx.domain.suite import CaseResult, Suite
from untaped_awx.infrastructure.specs import ALL_SPECS

_SUITE = Suite.model_validate(
    {"name": "suite-a", "jobTemplate": "jt", "cases": {"c1": {"launch": {}}}}
)


@pytest.mark.parametrize(
    ("row_keys", "expected"),
    [
        (lambda: Job.model_fields, "id"),
        (lambda: JobEvent.model_fields, "counter"),
        (lambda: WorkflowNode.model_fields, "id"),
        (lambda: CaseResult.model_fields, "suite"),
        (lambda: case_row(Path("s.yml"), _SUITE, "c1"), "suite"),
    ],
    ids=["Job", "JobEvent", "WorkflowNode", "CaseResult", "case_row"],
)
def test_row_source_first_key(row_keys: Callable[[], Mapping[str, object]], expected: str) -> None:
    assert next(iter(row_keys())) == expected


def test_awx_resource_spec_list_columns_leads_with_id() -> None:
    """Every spec-driven ``list`` (catalog-only stubs included) leads with ``id``."""
    assert [spec.kind for spec in ALL_SPECS if spec.list_columns[:1] != ("id",)] == []
