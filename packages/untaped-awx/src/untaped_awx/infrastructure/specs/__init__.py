"""Per-kind ResourceSpec instances + a registration helper.

Each module defines exactly one canonical :class:`ResourceSpec` (plus
"support" specs for FK-only kinds). Importing this package registers
every spec into the global tuple consumed by the catalog.
"""

from __future__ import annotations

from untaped_awx.infrastructure.spec import AwxResourceSpec
from untaped_awx.infrastructure.specs._support import (
    CREDENTIAL_TYPE_SPEC,
    EXECUTION_ENVIRONMENT_SPEC,
    INSTANCE_GROUP_SPEC,
    INVENTORY_SPEC,
    LABEL_SPEC,
    ORGANIZATION_SPEC,
)
from untaped_awx.infrastructure.specs.credential import CREDENTIAL_SPEC
from untaped_awx.infrastructure.specs.job_template import JOB_TEMPLATE_SPEC
from untaped_awx.infrastructure.specs.project import PROJECT_SPEC
from untaped_awx.infrastructure.specs.schedule import SCHEDULE_SPEC
from untaped_awx.infrastructure.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC

ALL_SPECS: tuple[AwxResourceSpec, ...] = (
    ORGANIZATION_SPEC,
    CREDENTIAL_TYPE_SPEC,
    CREDENTIAL_SPEC,
    PROJECT_SPEC,
    INVENTORY_SPEC,
    EXECUTION_ENVIRONMENT_SPEC,
    LABEL_SPEC,
    INSTANCE_GROUP_SPEC,
    JOB_TEMPLATE_SPEC,
    WORKFLOW_JOB_TEMPLATE_SPEC,
    SCHEDULE_SPEC,
)
"""Canonical ordering follows apply-time dependency order
(see ``AGENTS.md`` Apply ordering); the topological sort in
``apply_file._topological_sort`` uses this order as its tie-breaker."""

__all__ = [
    "ALL_SPECS",
    "CREDENTIAL_SPEC",
    "CREDENTIAL_TYPE_SPEC",
    "EXECUTION_ENVIRONMENT_SPEC",
    "INSTANCE_GROUP_SPEC",
    "INVENTORY_SPEC",
    "JOB_TEMPLATE_SPEC",
    "LABEL_SPEC",
    "ORGANIZATION_SPEC",
    "PROJECT_SPEC",
    "SCHEDULE_SPEC",
    "WORKFLOW_JOB_TEMPLATE_SPEC",
]
