"""Kind spelling transforms shared by selection, save, pipes, and strategies."""

from __future__ import annotations

import pytest

from untaped.capabilities.awx.domain.kinds import (
    UNIFIED_TEMPLATE_KINDS,
    pipe_kind,
    snake_kind,
    type_matches_kind,
    unified_template_kind,
)


@pytest.mark.parametrize(
    ("kind", "snake"),
    [
        ("JobTemplate", "job_template"),
        ("WorkflowJobTemplate", "workflow_job_template"),
        ("Project", "project"),
        ("HTTPRequest", "http_request"),
    ],
)
def test_snake_kind_is_acronym_aware(kind: str, snake: str) -> None:
    assert snake_kind(kind) == snake
    assert pipe_kind(kind) == f"awx.{snake}"


def test_type_matches_kind_ignores_case_and_underscores() -> None:
    assert type_matches_kind("job_template", "JobTemplate")
    assert type_matches_kind("JobTemplate", "JobTemplate")
    assert not type_matches_kind("job", "JobTemplate")


@pytest.mark.parametrize("kind", UNIFIED_TEMPLATE_KINDS)
def test_unified_template_kind_round_trips_awx_type(kind: str) -> None:
    assert unified_template_kind(snake_kind(kind)) == kind


@pytest.mark.parametrize("awx_type", [None, "", "job", "inventory", "system_job_template"])
def test_unified_template_kind_rejects_other_types(awx_type: str | None) -> None:
    assert unified_template_kind(awx_type) is None
