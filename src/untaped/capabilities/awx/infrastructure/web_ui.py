"""Links into the controller web UI for executions.

AAP 2.5+ (the ``/api/controller/`` API prefix) serves the controller UI
under ``/execution/``; AWX and older AAP use the ``/#/`` router.
"""

from __future__ import annotations

from untaped.capabilities.awx.domain import Job
from untaped.capabilities.awx.domain.job import JOB_ROUTES
from untaped.capabilities.awx.settings import AwxSettings


def job_ui_url(settings: AwxSettings, job: Job) -> str | None:
    """The execution's output page, or ``None`` without a ``base_url`` or for an unknown kind."""
    routes = JOB_ROUTES.get(job.kind)
    if settings.base_url is None or routes is None:
        return None
    root = "/execution/" if settings.api_prefix.startswith("/api/controller/") else "/#/"
    return f"{settings.base_url.rstrip('/')}{root}jobs/{routes.ui_type}/{job.id}/output"
