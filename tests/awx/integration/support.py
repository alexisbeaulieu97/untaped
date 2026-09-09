"""Shared data builders for AWX mutation CLI integration tests."""

from __future__ import annotations

import json
from typing import Any

KINDS = [
    ("job-templates", "job_templates"),
    ("workflow-templates", "workflow_job_templates"),
    ("projects", "projects"),
    ("schedules", "schedules"),
    ("hosts", "hosts"),
    ("groups", "groups"),
    ("inventories", "inventories"),
    ("inventory-sources", "inventory_sources"),
]


def seed(fake: Any, path: str) -> None:
    """Seed the shared fixed-target mutation scenario."""
    fake.seed("organizations", id=1, name="Default")
    fake.seed("inventories", id=2, name="prod", organization=1, kind="")
    fake.seed("projects", id=3, name="parent", organization=1)
    fake.seed(
        path,
        id=10,
        name="target",
        description="old",
        organization=1,
        inventory=2,
        unified_job_template=3,
        source="scm",
        kind="",
        summary_fields={
            "organization": {"id": 1, "name": "Default"},
            "inventory": {"id": 2, "name": "prod", "organization_name": "Default"},
            "unified_job_template": {
                "id": 3,
                "name": "parent",
                "unified_job_type": "project",
                "organization_name": "Default",
            },
        },
    )


def pipe(kind: str, id_: Any, name: str = "stale") -> str:
    """Encode one typed pipeline record for selection tests."""
    return json.dumps({"untaped": "1", "kind": kind, "record": {"id": id_, "name": name}}) + "\n"
