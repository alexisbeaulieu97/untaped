"""S17: what reading a controller into a lint snapshot costs, per kind, on a large fixture.

300 job templates (3 credentials, 2 labels and 1 instance group each, half
with a survey), 40 projects with playbooks, 10 workflows of 5 nodes (one
with an approval node), 50 schedules, with their organizations, inventories,
credentials and credential types; rows carry ``summary_fields`` as AWX
sends them. The counts are pinned: a loader change that moves one edits
:data:`MEASURED` in the same change, so its cost is a reviewed number.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from untaped.sdk.testing import compose_with, invoke_cli
from untaped_awx.cli import app

pytestmark = pytest.mark.usefixtures("fresh_composition")

TEMPLATES, PROJECTS, WORKFLOWS, NODES, SCHEDULES = 300, 40, 10, 5, 50

MEASURED: dict[str, int] = {
    "credential_types": 1,
    "credentials": 1,
    "execution_environments": 1,
    "groups": 1,
    "hosts": 1,
    "instance_groups": 1,
    "inventories": 1,
    "inventories instance_groups": 5,
    "inventory_sources": 1,
    "job_templates": 2,  # 300 rows: two pages of 200
    "job_templates credentials": 300,
    "job_templates instance_groups": 300,
    "job_templates labels": 300,
    "job_templates survey_spec": 300,
    "labels": 1,
    "organizations": 1,
    "projects": 1,
    "projects playbooks": 40,
    "schedules": 1,
    "system_job_templates": 1,
    "workflow_approval_templates detail": 1,
    "workflow_job_template_nodes credentials": 50,
    "workflow_job_template_nodes instance_groups": 50,
    "workflow_job_template_nodes labels": 50,
    "workflow_job_templates": 1,
    "workflow_job_templates labels": 10,
    "workflow_job_templates survey_spec": 10,
    "workflow_job_templates workflow_nodes": 10,
}
"""Requests by first path segment; ``<kind> <sub>`` for a ``/<kind>/<id>/<sub>/`` page."""


@pytest.fixture(autouse=True)
def _outside_this_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def composed(fresh_composition: None) -> Iterator[None]:
    with compose_with("awx", "lint"):
        yield


def _org(id_: int) -> dict[str, Any]:
    return {"organization": {"id": id_, "name": f"org-{id_}"}}


def seed_large(fake: Any) -> None:
    """The S17 controller (see the module docstring)."""
    _seed_shared(fake)
    _seed_templates(fake)
    _seed_workflows(fake)
    _seed_schedules(fake)


def _seed_shared(fake: Any) -> None:
    for id_ in (1, 2):
        fake.seed("organizations", id=id_, name=f"org-{id_}")
    for id_ in (1, 2):
        fake.seed("credential_types", id=id_, name=f"type-{id_}", kind="ssh")
    for id_ in range(1, 7):
        fake.seed(
            "credentials", id=id_, name=f"cred-{id_}", organization=1, credential_type=1,
            summary_fields=_org(1),
        )  # fmt: skip
    for id_ in range(1, 5):
        fake.seed("labels", id=id_, name=f"label-{id_}", organization=1, summary_fields=_org(1))
    for id_ in (1, 2):
        fake.seed("instance_groups", id=id_, name=f"group-{id_}")
    for id_ in range(1, 6):
        fake.seed(
            "inventories", id=id_, name=f"inv-{id_}", organization=1, kind="", total_hosts=3,
            summary_fields=_org(1),
        )  # fmt: skip
    for id_ in range(1, PROJECTS + 1):
        fake.seed(
            "projects", id=id_, name=f"project-{id_}", organization=1, status="successful",
            scm_type="git", scm_url=f"https://git.example.com/p{id_}.git",
            summary_fields=_org(1),
        )  # fmt: skip
        fake.playbooks[id_] = ["site.yml"]


def _seed_templates(fake: Any) -> None:
    for index in range(TEMPLATES):
        id_ = 1000 + index
        project, inventory = 1 + index % PROJECTS, 1 + index % 5
        surveyed = index % 2 == 0
        fake.seed(
            "job_templates", id=id_, name=f"template-{index}", organization=1,
            project=project, inventory=inventory, playbook="site.yml",
            survey_enabled=surveyed,
            survey_spec={"spec": [{"variable": "env", "type": "text", "required": False}]}
            if surveyed else {},
            summary_fields={
                **_org(1),
                "project": {"id": project, "name": f"project-{project}"},
                "inventory": {"id": inventory, "name": f"inv-{inventory}"},
            },
        )  # fmt: skip
        fake.memberships[("job_templates", id_, "credentials")] = {
            1 + index % 6, 1 + (index + 1) % 6, 1 + (index + 2) % 6,
        }  # fmt: skip
        fake.memberships[("job_templates", id_, "labels")] = {1 + index % 4, 1 + (index + 1) % 4}
        fake.memberships[("job_templates", id_, "instance_groups")] = {1 + index % 2}


def _seed_workflows(fake: Any) -> None:
    for index in range(WORKFLOWS):
        workflow = 3000 + index
        fake.seed(
            "workflow_job_templates", id=workflow, name=f"workflow-{index}", organization=1,
            summary_fields=_org(1),
        )  # fmt: skip
        for position in range(NODES):
            node = workflow * 10 + position
            approval = index == 0 and position == NODES - 1
            if approval:
                fake.seed("workflow_approval_templates", id=9000, name="sign-off", timeout=0)
            template = 9000 if approval else 1000 + (index * NODES + position) % TEMPLATES
            fake.seed(
                "workflow_nodes", id=node, identifier=f"step-{position}",
                workflow_job_template=workflow, unified_job_template=template,
                summary_fields={"unified_job_template": {
                    "id": template,
                    "unified_job_type": "workflow_approval" if approval else "job",
                }},
            )  # fmt: skip


def _seed_schedules(fake: Any) -> None:
    for index in range(SCHEDULES):
        template = 1000 + index
        fake.seed(
            "schedules", id=5000 + index, name=f"schedule-{index}",
            unified_job_template=template, rrule="DTSTART:20260101T000000Z RRULE:FREQ=DAILY",
            summary_fields={"unified_job_template": {
                "id": template, "name": f"template-{index}", "unified_job_type": "job",
                "organization_name": "org-1",
            }},
        )  # fmt: skip


def _group(path: str) -> str:
    parts = [part for part in path.removeprefix("/api/v2/").split("/") if part]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 3 and parts[1].isdigit():
        return f"{parts[0]} {parts[2]}"
    return f"{parts[0]} detail"


def measure(fake: Any) -> tuple[Counter[str], float, list[dict[str, Any]]]:
    """The fill's requests by group, its wall time, and the rows it printed."""
    started = time.perf_counter()
    result = invoke_cli(app, ["lint", "-f", "json"])
    elapsed = time.perf_counter() - started
    assert result.exit_code in (0, 3), result.output
    counts = Counter(_group(call.request.url.path) for call in fake.router.calls)
    return counts, elapsed, json.loads(result.stdout)


@pytest.mark.usefixtures("composed")
def test_controller_loader_request_budget(fake_aap: Any) -> None:
    seed_large(fake_aap)
    counts, _elapsed, rows = measure(fake_aap)  # the PR body reports the wall time
    assert all(call.request.method == "GET" for call in fake_aap.router.calls)
    details = [group for group in counts if group.endswith(" detail")]
    assert details == ["workflow_approval_templates detail"], counts  # no record read but these
    assert dict(sorted(counts.items())) == MEASURED
    assert not [row for row in rows if row["rule"] == "awx.unresolved-reference"]
