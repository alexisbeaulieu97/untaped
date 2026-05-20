"""End-to-end CLI tests for ``untaped awx workflow-templates nodes``."""

from __future__ import annotations

import json
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
            },
            "workflow_job_template": {"id": 100, "name": "weekly-rollup"},
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
            },
            "workflow_job_template": {"id": 100, "name": "weekly-rollup"},
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
            },
            "workflow_job_template": {"id": 200, "name": "nightly-backups"},
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
            },
            "workflow_job_template": {"id": 200, "name": "nightly-backups"},
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


def test_nodes_accepts_multiple_positional_roots(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "200",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    ids = [int(r) for r in result.stdout.strip().splitlines()]
    assert sorted(ids) == [1, 2, 3, 4]
    # Input-order pin: every root-100 id (1,2) precedes every root-200
    # id (3,4); within-root order is intentionally not constrained.
    last_root_100 = max(i for i, n in enumerate(ids) if n in {1, 2})
    first_root_200 = min(i for i, n in enumerate(ids) if n in {3, 4})
    assert last_root_100 < first_root_200


def test_nodes_stdin_reads_multiple_roots_and_concatenates(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "--stdin",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
        input="100\n200\n",
    )
    assert result.exit_code == 0, result.output
    ids = [int(r) for r in result.stdout.strip().splitlines()]
    assert sorted(ids) == [1, 2, 3, 4]
    last_root_100 = max(i for i, n in enumerate(ids) if n in {1, 2})
    first_root_200 = min(i for i, n in enumerate(ids) if n in {3, 4})
    assert last_root_100 < first_root_200


def test_nodes_stdin_rejects_positional_combo(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    result = CliRunner().invoke(
        app,
        ["workflow-templates", "nodes", "100", "--stdin"],
        input="200\n",
    )
    assert result.exit_code != 0
    assert "stdin" in result.stderr


def test_nodes_stdin_rejects_empty_input(fake_aap: Any) -> None:
    result = CliRunner().invoke(
        app,
        ["workflow-templates", "nodes", "--stdin"],
        input="",
    )
    assert result.exit_code != 0
    assert "stdin" in result.stderr


def test_nodes_positional_partial_failure_warns_and_exits_nonzero(
    fake_aap: Any,
) -> None:
    _seed_org_and_root_workflow(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "does-not-exist",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 1, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["1", "2"]
    assert "does-not-exist" in result.stderr
    assert "does-not-exist" not in result.stdout


def test_nodes_stdin_partial_failure_warns_and_exits_nonzero(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "--stdin",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
        input="100\ndoes-not-exist\n",
    )
    assert result.exit_code == 1, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["1", "2"]
    assert "does-not-exist" in result.stderr
    assert "does-not-exist" not in result.stdout


def test_nodes_filter_narrows_results(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--filter",
            "unified_job_template=10",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["1"]


def test_nodes_filter_repeatable(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    # Two filters compose with AND server-side: ``__in`` narrows to
    # nodes whose UJT is in {10, 200}; ``__gt`` then drops the UJT=10
    # row. Verifies both flags reach AWX, not just the first.
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--filter",
            "unified_job_template__in=10,200",
            "--filter",
            "unified_job_template__gt=11",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["2"]


def test_nodes_filter_with_recursive_applies_at_every_level(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    # ``unified_job_template__in=10,11,12,200`` matches: node 1 (JT 10)
    # at root, the sub-workflow node 2 (UJT 200) at root, and nodes
    # 3 (JT 11) + 4 (JT 12) inside the sub-workflow. Recursion succeeds
    # because the filter keeps the workflow-job-template row that lets
    # the BFS discover the child workflow.
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--recursive",
            "--filter",
            "unified_job_template__in=10,11,12,200",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["1", "2", "3", "4"]


def test_nodes_filter_with_recursive_prunes_sub_workflow_descent(
    fake_aap: Any,
) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    # ``unified_job_template__in=10,11`` excludes node 2 (UJT 200, the
    # sub-workflow link), so BFS never sees it and never descends into
    # workflow 200 — nodes 3, 4 are absent. Locks in the documented
    # pruning semantics: filters that exclude sub-workflow rows stop
    # the descent at that node.
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--recursive",
            "--filter",
            "unified_job_template__in=10,11",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["1"]


def test_nodes_filter_composes_with_depth_cap(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    # ``--depth 1`` descends one level, and the same filter applies at
    # both levels. UJT 11 is inside sub-workflow 200 (depth 1); UJT 12
    # is also there but excluded by the filter. Locks in that the
    # filter reaches the depth-capped recursion frontier.
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--depth",
            "1",
            "--filter",
            "unified_job_template__in=10,11,200",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    # Node 1 (UJT=10, depth 0), node 2 (UJT=200, depth 0), node 3
    # (UJT=11, depth 1). Node 4 (UJT=12) excluded by the filter.
    assert ids == ["1", "2", "3"]


def test_nodes_projects_summary_fields_parent_workflow_name(fake_aap: Any) -> None:
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
            "summary_fields.workflow_job_template.name",
            "--columns",
            "name",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = sorted(result.stdout.strip().splitlines())
    assert rows == [
        "weekly-rollup\tnightly-backups",
        "weekly-rollup\tsmoke-test",
    ]


def test_nodes_json_explicit_summary_fields_column_projects_correctly(
    fake_aap: Any,
) -> None:
    # ``-f json`` honours the default column set just like the other
    # formats — ``summary_fields`` only appears when the user opts in
    # via dotted-path projection. Pins that the column is reachable
    # via projection, with the correct per-row parent-workflow name.
    _seed_org_and_root_workflow(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "100",
            "--format",
            "json",
            "--columns",
            "id",
            "--columns",
            "summary_fields.workflow_job_template.name",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert {row["id"] for row in rows} == {1, 2}
    assert all(row["summary_fields.workflow_job_template.name"] == "weekly-rollup" for row in rows)


def test_nodes_recursive_summary_fields_carries_per_root_name(fake_aap: Any) -> None:
    # ``summary_fields.workflow_job_template.name`` tracks the *immediate*
    # parent workflow on each row, not the BFS root. Locks in that the
    # field travels through recursion with per-level fidelity.
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
            "summary_fields.workflow_job_template.name",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = sorted(result.stdout.strip().splitlines(), key=lambda r: int(r.split("\t")[0]))
    assert rows == [
        "1\tweekly-rollup",
        "2\tweekly-rollup",
        "3\tnightly-backups",
        "4\tnightly-backups",
    ]


def test_nodes_filter_malformed_rejected(fake_aap: Any) -> None:
    result = CliRunner().invoke(
        app,
        ["workflow-templates", "nodes", "100", "--filter", "no-equals-sign"],
    )
    assert result.exit_code != 0
    assert "--filter" in result.stderr


def test_nodes_stdin_recursive_type_filter_end_to_end(fake_aap: Any) -> None:
    _seed_org_and_root_workflow(fake_aap)
    _seed_nested(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "nodes",
            "--stdin",
            "--recursive",
            "--type",
            "job_template",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
        input="100\n",
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["1", "3", "4"]
