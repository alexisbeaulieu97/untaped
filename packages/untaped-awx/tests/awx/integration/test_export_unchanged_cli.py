"""``awx export --all-kinds`` writes the same documents, byte for byte, on every kind.

The golden files under ``export_golden/`` pin export's output before the
controller reads behind ``snapshot_from_record`` change shape (fewer requests
for the same document), so a cut that loses a field fails here.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from untaped.testing import CliInvoker
from untaped_awx.cli import app

GOLDEN = Path(__file__).parent / "export_golden"

_ORG = {"organization": {"id": 1, "name": "Default"}}
_SURVEY = {
    "name": "",
    "description": "",
    "spec": [
        {"variable": "version", "type": "text", "required": True, "default": ""},
        {"variable": "db_password", "type": "password", "required": False, "default": "hunter2"},
    ],
}


def _seed(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed("organizations", id=2, name="Ops")
    fake.seed("credential_types", id=3, name="Machine", kind="ssh")
    fake.seed("credentials", id=40, name="ssh", organization=1, credential_type=3)
    fake.seed("credentials", id=41, name="vault", organization=1, credential_type=4)
    fake.seed("credentials", id=42, name="git", organization=1, credential_type=5)
    fake.seed("execution_environments", id=60, name="ee-default", image="quay.io/ee:1")
    fake.seed("labels", id=70, name="prod", organization=1)
    fake.seed("instance_groups", id=80, name="default")
    fake.seed("instance_groups", id=81, name="fallback")
    fake.seed("system_job_templates", id=90, name="Cleanup Job Details")

    fake.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        description="Main playbooks",
        scm_type="git",
        scm_url="https://git.example.com/playbooks.git",
        scm_branch="main",
        allow_override=True,
        credential=42,
        default_environment=60,
        local_path="_10__playbooks",
        status="successful",
        summary_fields=_ORG,
    )
    fake.seed("projects", id=11, name="manual", organization=1, scm_type="", local_path="manual")

    fake.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        kind="",
        variables="---\nregion: eu\n",
        total_hosts=2,
        summary_fields=_ORG,
    )
    fake.seed("inventories", id=21, name="staging", organization=1, kind="", variables="")
    combined = {"name": "combined", "organization": 1, "kind": "constructed"}
    fake.seed("inventories", id=22, host_filter="", **combined)
    fake.seed(
        "constructed_inventories",
        id=22,
        source_vars="plugin: constructed\n",
        update_cache_timeout=60,
        limit="web*",
        verbosity=1,
        **combined,
    )
    fake.memberships[("inventories", 20, "instance_groups")] = {81, 80}
    fake.memberships[("inventories", 22, "input_inventories")] = {21, 20}

    fake.seed(
        "inventory_sources",
        id=30,
        name="from-git",
        inventory=20,
        source="scm",
        source_project=10,
        source_path="inventory/hosts.yml",
        source_vars="---\ncompose: {}\n",
        overwrite=True,
        summary_fields={"inventory": {"id": 20, "name": "prod", "organization_name": "Default"}},
    )
    fake.seed(
        "inventory_sources",
        id=31,
        name="combined-source",
        inventory=22,
        source="constructed",
        source_vars="plugin: constructed\n",
        update_cache_timeout=60,
        limit="web*",
        verbosity=1,
    )

    prod = {
        "summary_fields": {"inventory": {"id": 20, "name": "prod", "organization_name": "Default"}}
    }
    fake.seed("hosts", id=50, name="web-01", inventory=20, enabled=True, variables="{}", **prod)
    fake.seed(
        "hosts",
        id=51,
        name="web-02",
        inventory=20,
        enabled=False,
        variables="ansible_host: 10.0.0.2\n",
    )
    fake.seed("groups", id=55, name="web", inventory=20, variables="", **prod)
    fake.seed("groups", id=56, name="web-eu", inventory=20, description="EU web", **prod)
    fake.memberships[("groups", 55, "hosts")] = {50, 51}
    fake.memberships[("groups", 55, "children")] = {56}

    fake.seed(
        "job_templates",
        id=100,
        name="deploy",
        organization=1,
        description="Deploy the app",
        job_type="run",
        project=10,
        inventory=20,
        playbook="deploy.yml",
        scm_branch="",
        forks=5,
        extra_vars="---\nversion: 3\npassword: hunter2\n",
        host_config_key="callback-key",
        execution_environment=60,
        ask_inventory_on_launch=True,
        survey_enabled=True,
        survey_spec=_SURVEY,
        last_job_run="2026-01-01T00:00:00Z",
        summary_fields=_ORG,
    )
    fake.memberships[("job_templates", 100, "credentials")] = {40, 41}
    fake.memberships[("job_templates", 100, "labels")] = {70}
    fake.memberships[("job_templates", 100, "instance_groups")] = {81, 80}
    fake.seed(
        "job_templates",
        id=101,
        name="survey-off",
        organization=1,
        project=10,
        inventory=21,
        playbook="site.yml",
        survey_enabled=False,
        survey_spec=_SURVEY,
    )
    fake.seed("job_templates", id=102, name="orphan", organization=None, playbook="ping.yml")

    fake.seed(
        "workflow_job_templates",
        id=200,
        name="release",
        organization=1,
        description="Build, deploy, verify",
        inventory=20,
        extra_vars="{}",
        survey_enabled=False,
        ask_limit_on_launch=True,
        summary_fields=_ORG,
    )
    fake.memberships[("workflow_job_templates", 200, "labels")] = {70}
    fake.seed("workflow_job_templates", id=201, name="nested", organization=2)
    fake.seed("workflow_approval_templates", id=300, name="Approve", timeout=600)
    nodes = {
        210: {"identifier": "approve", "unified_job_template": 300, "success_nodes": [211]},
        211: {"identifier": "deploy", "unified_job_template": 100, "inventory": 21,
              "limit": "web*", "extra_data": {"version": 4},
              "success_nodes": [212, 213], "failure_nodes": [214]},
        212: {"identifier": "sync-project", "unified_job_template": 10},
        213: {"identifier": "sync-source", "unified_job_template": 30,
              "always_nodes": [215]},
        214: {"identifier": "cleanup", "unified_job_template": 90},
        215: {"identifier": "nested", "unified_job_template": 201,
              "all_parents_must_converge": True},
        216: {"identifier": "gone", "unified_job_template": None},
    }  # fmt: skip
    for node_id, fields in nodes.items():
        fake.seed("workflow_nodes", id=node_id, workflow_job_template=200, **fields)
    fake.memberships[("workflow_job_template_nodes", 211, "credentials")] = {40}

    # AWX names a schedule's template by its job type ("job"), so most parents are
    # read back through the unified view.
    fake.seed("unified_job_templates", id=100, name="deploy", type="job_template")
    fake.seed("unified_job_templates", id=30, name="from-git", type="inventory_source")
    fake.seed(
        "schedules",
        id=400,
        name="nightly",
        unified_job_template=100,
        rrule="DTSTART:20260101T000000Z RRULE:FREQ=DAILY",
        enabled=True,
        extra_data={"version": 5, "db_password": "$encrypted$"},
        inventory=21,
        next_run="2026-01-02T00:00:00Z",
        summary_fields={
            "unified_job_template": {
                "id": 100,
                "name": "deploy",
                "unified_job_type": "job",
                "organization_name": "Default",
            }
        },
    )
    fake.seed(
        "schedules",
        id=401,
        name="hourly-sync",
        unified_job_template=10,
        rrule="DTSTART:20260101T000000Z RRULE:FREQ=HOURLY",
        enabled=False,
        summary_fields={
            "unified_job_template": {
                "id": 10,
                "name": "playbooks",
                "unified_job_type": "project",
                "organization_name": "Default",
            }
        },
    )
    fake.seed(
        "schedules",
        id=402,
        name="refresh",
        unified_job_template=30,
        rrule="DTSTART:20260101T000000Z RRULE:FREQ=WEEKLY",
        enabled=True,
        summary_fields={
            "unified_job_template": {
                "id": 30,
                "name": "from-git",
                "unified_job_type": "inventory_update",
            }
        },
    )


def test_export_every_kind_is_byte_identical(fake_aap: Any, tmp_path: Path) -> None:
    """Every exported file equals its golden copy, and no file is added or missing.

    To regenerate after an intended export change, run this test with
    ``AWX_EXPORT_GOLDEN=write`` and review the diff of ``export_golden/``.
    """
    _seed(fake_aap)
    out_dir = tmp_path / "out"

    result = CliInvoker().invoke(app, ["export", "--all-kinds", "--out-dir", str(out_dir)])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    if os.environ.get("AWX_EXPORT_GOLDEN") == "write":
        shutil.rmtree(GOLDEN, ignore_errors=True)
        shutil.copytree(out_dir, GOLDEN)
        pytest.skip("golden files rewritten")
    written = sorted(path.name for path in out_dir.iterdir())
    assert written == sorted(path.name for path in GOLDEN.iterdir())
    for name in written:
        assert (out_dir / name).read_bytes() == (GOLDEN / name).read_bytes(), name
