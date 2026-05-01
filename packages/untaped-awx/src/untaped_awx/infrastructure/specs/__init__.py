"""Per-kind ResourceSpec instances + a registration helper.

Each module defines exactly one canonical :class:`ResourceSpec` (plus
"support" specs for FK-only kinds). Importing this package registers
every spec into the global tuple consumed by the catalog.
"""

from __future__ import annotations

from untaped_awx.domain import ResourceSpec
from untaped_awx.infrastructure.specs._support import (
    CREDENTIAL_TYPE_SPEC,
    INVENTORY_SPEC,
    ORGANIZATION_SPEC,
)
from untaped_awx.infrastructure.specs.credential import CREDENTIAL_SPEC
from untaped_awx.infrastructure.specs.job_template import JOB_TEMPLATE_SPEC
from untaped_awx.infrastructure.specs.project import PROJECT_SPEC
from untaped_awx.infrastructure.specs.schedule import SCHEDULE_SPEC
from untaped_awx.infrastructure.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC

ALL_SPECS: tuple[ResourceSpec, ...] = (
    ORGANIZATION_SPEC,
    INVENTORY_SPEC,
    CREDENTIAL_TYPE_SPEC,
    CREDENTIAL_SPEC,
    PROJECT_SPEC,
    JOB_TEMPLATE_SPEC,
    WORKFLOW_JOB_TEMPLATE_SPEC,
    SCHEDULE_SPEC,
)
"""Canonical ordering follows apply-time dependency order."""

__all__ = [
    "ALL_SPECS",
    "CREDENTIAL_SPEC",
    "CREDENTIAL_TYPE_SPEC",
    "INVENTORY_SPEC",
    "JOB_TEMPLATE_SPEC",
    "ORGANIZATION_SPEC",
    "PROJECT_SPEC",
    "SCHEDULE_SPEC",
    "WORKFLOW_JOB_TEMPLATE_SPEC",
]
