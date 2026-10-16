"""Per-kind ResourceSpec instances + a registration helper.

Each module defines exactly one canonical :class:`ResourceSpec` (plus
"support" specs for FK-only kinds). Importing this package registers
every spec into the global tuple consumed by the catalog.
"""

from __future__ import annotations

from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.infrastructure.resources.specs._support import (
    CREDENTIAL_TYPE_SPEC,
    EXECUTION_ENVIRONMENT_SPEC,
    INSTANCE_GROUP_SPEC,
    LABEL_SPEC,
    ORGANIZATION_SPEC,
    SYSTEM_JOB_TEMPLATE_SPEC,
    UNIVERSAL_READ_ONLY,
)
from untaped_awx.infrastructure.resources.specs.credential import CREDENTIAL_SPEC
from untaped_awx.infrastructure.resources.specs.group import GROUP_SPEC
from untaped_awx.infrastructure.resources.specs.host import HOST_SPEC
from untaped_awx.infrastructure.resources.specs.inventory import INVENTORY_SPEC
from untaped_awx.infrastructure.resources.specs.inventory_source import INVENTORY_SOURCE_SPEC
from untaped_awx.infrastructure.resources.specs.job_template import JOB_TEMPLATE_SPEC
from untaped_awx.infrastructure.resources.specs.project import PROJECT_SPEC
from untaped_awx.infrastructure.resources.specs.schedule import SCHEDULE_SPEC
from untaped_awx.infrastructure.resources.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC

ALL_SPECS: tuple[AwxResourceSpec, ...] = (
    ORGANIZATION_SPEC,
    CREDENTIAL_TYPE_SPEC,
    CREDENTIAL_SPEC,
    PROJECT_SPEC,
    INVENTORY_SPEC,
    INVENTORY_SOURCE_SPEC,
    HOST_SPEC,
    GROUP_SPEC,
    EXECUTION_ENVIRONMENT_SPEC,
    LABEL_SPEC,
    INSTANCE_GROUP_SPEC,
    SYSTEM_JOB_TEMPLATE_SPEC,
    JOB_TEMPLATE_SPEC,
    WORKFLOW_JOB_TEMPLATE_SPEC,
    SCHEDULE_SPEC,
)
"""Canonical ordering follows apply-time dependency order; the topological
sort in ``apply_ordering.topological_sort`` uses this order as its tie-breaker."""

__all__ = [
    "ALL_SPECS",
    "CREDENTIAL_SPEC",
    "GROUP_SPEC",
    "HOST_SPEC",
    "INVENTORY_SOURCE_SPEC",
    "INVENTORY_SPEC",
    "JOB_TEMPLATE_SPEC",
    "PROJECT_SPEC",
    "SCHEDULE_SPEC",
    "UNIVERSAL_READ_ONLY",
]
