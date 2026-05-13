"""End-to-end CLI tests for ``untaped awx workflow-templates nodes``."""

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
            "id",
            "--columns",
            "identifier",
            "--columns",
            "name",
            "--columns",
            "type",
            "--columns",
            "depth",
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
            "id",
            "--columns",
            "depth",
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


def test_nodes_type_filter_keeps_only_matching_kind(fake_aap: Any) -> None:
    # ``--type job_template`` with ``--recursive`` must still descend into
    # workflow nodes so nested job templates surface — the filter is on
    # the output, not the traversal.
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--recursive",
            "--type",
            "job_template",
            "--format",
            "raw",
            "--columns",
            "id",
            "--columns",
            "type",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = sorted(result.stdout.strip().splitlines(), key=lambda r: int(r.split("\t")[0]))
    assert rows == [
        "1\tjob_template",
        "3\tjob_template",
        "4\tjob_template",
    ]


def test_nodes_type_filter_keeps_only_workflows(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--recursive",
            "--type",
            "workflow_job_template",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["2"]


def test_nodes_cycle_emits_stderr_warning(fake_aap: Any) -> None:
    # A → B → A. The use case skips re-entry and warns; the CLI must
    # forward that warning to stderr (not stdout) so pipelines stay clean.
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "workflow_job_templates",
        id=100,
        name="alpha",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "workflow_job_templates",
        id=200,
        name="beta",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "workflow_nodes",
        id=1,
        identifier="a-to-b",
        workflow_job_template=100,
        unified_job_template=200,
        summary_fields={
            "unified_job_template": {
                "id": 200,
                "name": "beta",
                "unified_job_type": "workflow_job",
            },
        },
    )
    fake_aap.seed(
        "workflow_nodes",
        id=2,
        identifier="b-to-a",
        workflow_job_template=200,
        unified_job_template=100,
        summary_fields={
            "unified_job_template": {
                "id": 100,
                "name": "alpha",
                "unified_job_type": "workflow_job",
            },
        },
    )
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
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "cycle" in result.stderr
    assert "100" in result.stderr
    assert "cycle" not in result.stdout


def test_nodes_rejects_negative_depth(fake_aap: Any) -> None:
    result = CliRunner().invoke(
        app,
        ["workflow-templates", "nodes", "100", "--depth", "-1"],
    )
    assert result.exit_code != 0


def test_nodes_rejects_unknown_type_value(fake_aap: Any) -> None:
    # ``--type`` is a Literal; a typo must fail at parse time, not
    # silently return an empty result set.
    result = CliRunner().invoke(
        app,
        ["workflow-templates", "nodes", "100", "--type", "job-template"],
    )
    assert result.exit_code != 0
