"""Links into the controller web UI for executions.

AAP 2.5+ (the ``/api/controller/`` API prefix) serves the controller UI
under ``/execution/``; AWX and older AAP use the ``/#/`` router.
"""

from __future__ import annotations

from untaped.capabilities.awx.domain import Job
from untaped.capabilities.awx.settings import AwxSettings

_UI_JOB_TYPES = {
    "job": "playbook",
    "workflow_job": "workflow",
    "project_update": "project",
    "inventory_update": "inventory",
    "ad_hoc_command": "command",
}


def job_ui_url(settings: AwxSettings, job: Job) -> str | None:
    """The execution's output page, or ``None`` without a ``base_url``."""
    if settings.base_url is None:
        return None
    root = "/execution/" if settings.api_prefix.startswith("/api/controller/") else "/#/"
    job_type = _UI_JOB_TYPES.get(job.kind, job.kind)
    return f"{settings.base_url.rstrip('/')}{root}jobs/{job_type}/{job.id}/output"
