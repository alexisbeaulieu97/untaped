"""Links into the controller web UI for executions.

AAP 2.5+ (the ``/api/controller/`` API prefix) serves the controller UI
under ``/execution/``; AWX and older AAP use the ``/#/`` router.
"""

from __future__ import annotations

from untaped_awx.domain import Job
from untaped_awx.domain.jobs.job import JOB_ROUTES
from untaped_awx.settings import AwxSettings


def job_ui_url(settings: AwxSettings, job: Job) -> str | None:
    """The execution's output page, or ``None`` without a server or for an unknown kind."""
    routes = JOB_ROUTES.get(job.kind)
    if settings.server is None or routes is None:
        return None
    root = "/execution/" if settings.api_prefix.startswith("/api/controller/") else "/#/"
    base = str(settings.server.url).rstrip("/")
    return f"{base}{root}jobs/{routes.ui_type}/{job.id}/output"
