from __future__ import annotations

from enum import Enum


class ResourceType(str, Enum):
    """Supported resource types for the MVP."""

    JOB_TEMPLATE = "job_template"
    WORKFLOW_JOB_TEMPLATE = "workflow_job_template"


class JobType(str, Enum):
    """Execution modes supported by Ansible job templates."""

    RUN = "run"
    CHECK = "check"
    SCAN = "scan"
