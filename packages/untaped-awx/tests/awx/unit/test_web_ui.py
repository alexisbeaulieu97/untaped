"""Controller web UI links for executions."""

from __future__ import annotations

import pytest

from untaped_awx.domain import Job
from untaped_awx.infrastructure.web_ui import job_ui_url
from untaped_awx.settings import AwxSettings


@pytest.mark.parametrize(
    ("api_prefix", "kind", "url"),
    [
        ("/api/controller/v2/", "job", "https://aap.example.com/execution/jobs/playbook/7/output"),
        ("/api/v2/", "job", "https://aap.example.com/#/jobs/playbook/7/output"),
        ("/api/v2/", "workflow_job", "https://aap.example.com/#/jobs/workflow/7/output"),
    ],
)
def test_job_ui_url_follows_the_ui_generation(api_prefix: str, kind: str, url: str) -> None:
    settings = AwxSettings(base_url="https://aap.example.com/", api_prefix=api_prefix)
    assert job_ui_url(settings, Job(id=7, kind=kind, status="successful")) == url


def test_job_ui_url_needs_a_base_url() -> None:
    assert job_ui_url(AwxSettings(), Job(id=7, kind="job", status="successful")) is None
