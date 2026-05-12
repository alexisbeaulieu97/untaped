"""End-to-end CLI tests for ``untaped awx workflow-templates nodes``.

``FakeAap``'s generic ``_sub_list`` handler serves ``GET
/workflow_job_templates/<id>/workflow_nodes/`` for free: it falls
through to the FK-column fallback (``workflow_job_template == <id>``)
which matches whatever we seed under ``self.store["workflow_nodes"]``.
No fixture changes are needed beyond the seed helpers below.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner
from untaped_awx import app

pytestmark = pytest.mark.integration


def _seed_org_and_root_workflow(fake: Any) -> None:
    """Seed an org + a root workflow with two children: one JT, one WJT."""
    fake.seed("organizations", id=1, name="Default")
    fake.seed(
        "workflow_job_templates",
        id=100,
        name="weekly-rollup",
        organization=1,
        organization_name="Default",
    )
    fake.seed(
        "job_templates",
        id=10,
        name="smoke-test",
        organization=1,
        organization_name="Default",
    )
    fake.seed(
        "workflow_job_templates",
        id=200,
        name="nightly-backups",
        organization=1,
        organization_name="Default",
    )
    fake.seed(
        "workflow_nodes",
        id=1,
        identifier="pre-flight",
        workflow_job_template=100,
        unified_job_template=10,
        summary_fields={
            "unified_job_template": {
                "id": 10,
                "name": "smoke-test",
                "unified_job_type": "job",
            }
        },
    )
    fake.seed(
        "workflow_nodes",
        id=2,
        identifier="rollup",
        workflow_job_template=100,
        unified_job_template=200,
        summary_fields={
            "unified_job_template": {
                "id": 200,
                "name": "nightly-backups",
                "unified_job_type": "workflow_job",
            }
        },
    )


def _seed_nested(fake: Any) -> None:
    """Seed two more nodes under the nested workflow (id 200)."""
    fake.seed(
        "job_templates",
        id=11,
        name="db-backup",
        organization=1,
        organization_name="Default",
    )
    fake.seed(
        "job_templates",
        id=12,
        name="fs-backup",
        organization=1,
        organization_name="Default",
    )
    fake.seed(
        "workflow_nodes",
        id=3,
        identifier="db",
        workflow_job_template=200,
        unified_job_template=11,
        summary_fields={
            "unified_job_template": {
                "id": 11,
                "name": "db-backup",
                "unified_job_type": "job",
            }
        },
    )
    fake.seed(
        "workflow_nodes",
        id=4,
        identifier="fs",
        workflow_job_template=200,
        unified_job_template=12,
        summary_fields={
            "unified_job_template": {
                "id": 12,
                "name": "fs-backup",
                "unified_job_type": "job",
            }
        },
    )


def test_nodes_lists_top_level_by_id(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--format",
            "raw",
            "--columns",
            "id,identifier,name,type,depth",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = sorted(result.stdout.strip().splitlines())
    assert rows == [
        "1\tpre-flight\tsmoke-test\tjob_template\t0",
        "2\trollup\tnightly-backups\tworkflow_job_template\t0",
    ]


def test_nodes_resolves_workflow_by_name(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "weekly-rollup",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["1", "2"]


def test_nodes_unknown_workflow_exits_nonzero(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    result = CliRunner().invoke(
        app,
        ["workflow-templates", "nodes", "does-not-exist"],
    )
    assert result.exit_code != 0


def test_nodes_recursive_flattens_sub_workflow(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--recursive",
            "--format",
            "raw",
            "--columns",
            "id,depth",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = sorted(result.stdout.strip().splitlines(), key=lambda r: int(r.split("\t")[0]))
    assert rows == ["1\t0", "2\t0", "3\t1", "4\t1"]


def test_nodes_depth_zero_returns_only_root(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--recursive",
            "--depth",
            "0",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["1", "2"]


def test_nodes_depth_one_caps_nested(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    # Without ``--recursive``, ``--depth 1`` implicitly enables recursion.
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--depth",
            "1",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["1", "2", "3", "4"]


def test_nodes_rejects_negative_depth(fake_aap: Any) -> None:
    result = CliRunner().invoke(
        app,
        ["workflow-templates", "nodes", "100", "--depth", "-1"],
    )
    assert result.exit_code != 0
