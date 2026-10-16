"""Shared fixtures for the ``awx lint`` tests: apply documents and a controller to seed.

Both hold the same resources, written once each way, so a rule's test can
check it fires on the origins it should. Every name is synthetic.
"""

from __future__ import annotations

from typing import Any

DOCUMENTS = """\
kind: Project
metadata: {name: web, organization: Platform}
spec:
  scm_type: git
  scm_url: https://git.example.com/web.git
  scm_branch: main
  allow_override: false
---
kind: Inventory
metadata: {name: prod, organization: Platform}
spec: {}
---
kind: JobTemplate
metadata: {name: deploy-web, organization: Platform}
spec:
  project: web
  inventory: prod
  playbook: deploy.yml
  scm_branch: feature
  ask_limit_on_launch: false
  survey_enabled: true
  survey_spec:
    name: ""
    description: ""
    spec:
      - {variable: region, type: text, required: true, question_name: Region}
---
kind: Schedule
metadata:
  name: nightly
  parent: {kind: JobTemplate, name: deploy-web, organization: Platform}
spec:
  rrule: DTSTART:20260101T000000Z RRULE:FREQ=DAILY
  limit: web1
"""
"""A batch where three rules fire, every reference resolving within it: ``deploy-web``'s branch
its project ignores, and ``nightly``'s limit ``deploy-web`` ignores and its unanswered required
question."""

CLEAN = """\
kind: Project
metadata: {name: web, organization: Platform}
spec: {scm_type: git, scm_url: "https://git.example.com/web.git", allow_override: true}
---
kind: Inventory
metadata: {name: prod, organization: Platform}
spec: {}
---
kind: JobTemplate
metadata: {name: deploy-web, organization: Platform}
spec: {project: web, inventory: prod, playbook: deploy.yml}
"""
"""A batch no awx rule fires on."""

_PLATFORM = {"organization": {"id": 1, "name": "Platform"}}


def seed_controller(fake: Any) -> None:
    """A controller where every controller-origin rule fires once.

    ``deploy-web`` runs a playbook its project does not have, on a branch its
    project will not run; ``nightly`` sets a limit ``deploy-web`` ignores;
    ``prod`` has no hosts; ``orphan`` has no project; ``release`` has a node
    whose template is gone.
    """
    fake.seed("organizations", id=1, name="Platform")
    fake.seed(
        "projects",
        id=10,
        name="web",
        organization=1,
        scm_type="git",
        scm_url="https://git.example.com/web.git",
        scm_branch="main",
        allow_override=False,
        status="successful",
        last_job_failed=False,
        summary_fields=_PLATFORM,
    )
    fake.playbooks[10] = ["site.yml"]
    fake.seed(
        "inventories", id=20, name="prod", organization=1, kind="", total_hosts=0,
        variables="ansible_password: swordfish\nansible_user: deploy\n",
        summary_fields=_PLATFORM,
    )  # fmt: skip
    fake.seed(
        "job_templates",
        id=100,
        name="deploy-web",
        organization=1,
        project=10,
        inventory=20,
        playbook="deploy.yml",
        scm_branch="feature",
        ask_limit_on_launch=False,
        extra_vars='{"db_password": "hunter2", "region": "eu"}',
        summary_fields=_PLATFORM,
    )
    fake.seed(
        "job_templates",
        id=101,
        name="orphan",
        organization=1,
        project=None,
        inventory=20,
        playbook="site.yml",
        summary_fields=_PLATFORM,
    )
    fake.seed(
        "schedules",
        id=400,
        name="nightly",
        unified_job_template=100,
        rrule="DTSTART:20260101T000000Z RRULE:FREQ=DAILY",
        limit="web1",
        summary_fields={
            "unified_job_template": {
                "id": 100,
                "name": "deploy-web",
                "unified_job_type": "job",
                "organization_name": "Platform",
            }
        },
    )
    fake.seed("workflow_job_templates", id=200, name="release", organization=1)
    fake.seed(
        "workflow_nodes",
        id=210,
        identifier="deploy",
        workflow_job_template=200,
        unified_job_template=100,
        summary_fields={"unified_job_template": {"id": 100, "unified_job_type": "job"}},
    )
    fake.seed(
        "workflow_nodes",
        id=211,
        identifier="gone",
        workflow_job_template=200,
        unified_job_template=None,
    )


__all__ = ["CLEAN", "DOCUMENTS", "seed_controller"]
