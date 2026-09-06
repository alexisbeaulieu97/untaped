"""End-to-end CLI tests for ``untaped awx job-templates/workflow-templates usage``."""

from __future__ import annotations

import re
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def _seed_node(
    fake: Any,
    *,
    node_id: int,
    wf_id: int,
    wf_name: str,
    ujt_id: int,
    ujt_name: str,
    ujt_type: str = "job",
) -> None:
    fake.seed(
        "workflow_nodes",
        id=node_id,
        workflow_job_template=wf_id,
        unified_job_template=ujt_id,
        summary_fields={
            "unified_job_template": {
                "id": ujt_id,
                "name": ujt_name,
                "unified_job_type": ujt_type,
            },
            "workflow_job_template": {"id": wf_id, "name": wf_name},
        },
    )


def _seed_usage_graph(fake: Any) -> None:
    """JT 10 runs inside WF 200; WF 200 runs inside WF 100."""
    fake.seed("organizations", id=1, name="Default")
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
        "workflow_job_templates",
        id=100,
        name="weekly-rollup",
        organization=1,
        organization_name="Default",
    )
    _seed_node(
        fake,
        node_id=1,
        wf_id=200,
        wf_name="nightly-backups",
        ujt_id=10,
        ujt_name="smoke-test",
    )
    _seed_node(
        fake,
        node_id=2,
        wf_id=100,
        wf_name="weekly-rollup",
        ujt_id=200,
        ujt_name="nightly-backups",
        ujt_type="workflow_job",
    )


def test_usage_repeatable_columns_contract(fake_aap: Any) -> None:
    """Repeated ``--columns`` flags select the column set in order."""
    _seed_usage_graph(fake_aap)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "usage",
            "--by-id",
            "10",
            "--format",
            "raw",
            "--columns",
            "id",
            "--columns",
            "name",
            "--columns",
            "depth",
            "--columns",
            "node_count",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["200\tnightly-backups\t0\t1"]


def test_usage_lists_direct_parents_by_id(fake_aap: Any) -> None:
    _seed_usage_graph(fake_aap)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "usage",
            "--by-id",
            "10",
            "--format",
            "raw",
            "--columns",
            "id",
            "--columns",
            "name",
            "--columns",
            "depth",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["200\tnightly-backups\t0"]


def test_usage_resolves_job_template_by_name(fake_aap: Any) -> None:
    _seed_usage_graph(fake_aap)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "usage",
            "smoke-test",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "200"


def test_usage_on_workflow_templates_sub_app(fake_aap: Any) -> None:
    _seed_usage_graph(fake_aap)
    result = CliInvoker().invoke(
        app,
        [
            "workflow-templates",
            "usage",
            "nightly-backups",
            "--format",
            "raw",
            "--columns",
            "id",
            "--columns",
            "name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["100\tweekly-rollup"]


def test_usage_recursive_walks_up_ancestry_with_depth(fake_aap: Any) -> None:
    _seed_usage_graph(fake_aap)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "usage",
            "--by-id",
            "10",
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
    assert result.stdout.strip().splitlines() == ["200\t0", "100\t1"]


def test_usage_depth_caps_ancestry_walk(fake_aap: Any) -> None:
    _seed_usage_graph(fake_aap)
    fake_aap.seed(
        "workflow_job_templates",
        id=300,
        name="quarterly-audit",
        organization=1,
        organization_name="Default",
    )
    _seed_node(
        fake_aap,
        node_id=3,
        wf_id=300,
        wf_name="quarterly-audit",
        ujt_id=100,
        ujt_name="weekly-rollup",
        ujt_type="workflow_job",
    )
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "usage",
            "--by-id",
            "10",
            "--depth",
            "1",
            "--format",
            "raw",
            "--columns",
            "id",
            "--columns",
            "depth",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["200\t0", "100\t1"]


def test_usage_counts_multiple_references_in_one_workflow(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "job_templates",
        id=10,
        name="smoke-test",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "workflow_job_templates",
        id=100,
        name="weekly-rollup",
        organization=1,
        organization_name="Default",
    )
    for node_id in (1, 2):
        _seed_node(
            fake_aap,
            node_id=node_id,
            wf_id=100,
            wf_name="weekly-rollup",
            ujt_id=10,
            ujt_name="smoke-test",
        )
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "usage",
            "--by-id",
            "10",
            "--format",
            "raw",
            "--columns",
            "id",
            "--columns",
            "node_count",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["100\t2"]


def test_usage_zero_usages_prints_empty_and_exits_zero(fake_aap: Any) -> None:
    _seed_usage_graph(fake_aap)
    fake_aap.seed(
        "job_templates",
        id=11,
        name="unused",
        organization=1,
        organization_name="Default",
    )
    result = CliInvoker().invoke(
        app,
        ["job-templates", "usage", "unused", "--format", "raw", "--columns", "id"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""


def test_usage_unknown_template_exits_nonzero(fake_aap: Any) -> None:
    _seed_usage_graph(fake_aap)
    result = CliInvoker().invoke(
        app,
        ["job-templates", "usage", "does-not-exist"],
    )
    assert result.exit_code != 0


def test_usage_cycle_emits_stderr_warning(fake_aap: Any) -> None:
    # A (100) contains B (200); B contains A. Walking B's ancestry finds
    # A, then A's parents include B again — the warning goes to stderr
    # so pipelines stay clean.
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
    _seed_node(
        fake_aap,
        node_id=1,
        wf_id=100,
        wf_name="alpha",
        ujt_id=200,
        ujt_name="beta",
        ujt_type="workflow_job",
    )
    _seed_node(
        fake_aap,
        node_id=2,
        wf_id=200,
        wf_name="beta",
        ujt_id=100,
        ujt_name="alpha",
        ujt_type="workflow_job",
    )
    result = CliInvoker().invoke(
        app,
        [
            "workflow-templates",
            "usage",
            "--by-id",
            "200",
            "--recursive",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["100"]
    assert "cycle" in result.stderr
    assert "cycle" not in result.stdout


def test_usage_accepts_multiple_positional_roots(fake_aap: Any) -> None:
    _seed_usage_graph(fake_aap)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "usage",
            "--by-id",
            "10",
            "10",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    # Dedup is per root: the same template queried twice emits its
    # parent twice, in input order.
    assert result.stdout.strip().splitlines() == ["200", "200"]


def test_usage_stdin_reads_roots_and_partial_failure_exits_nonzero(
    fake_aap: Any,
) -> None:
    _seed_usage_graph(fake_aap)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "usage",
            "--stdin",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
        input="smoke-test\ndoes-not-exist\n",
    )
    assert result.exit_code == 1, result.output
    assert result.stdout.strip().splitlines() == ["200"]
    assert "does-not-exist" in result.stderr
    assert "does-not-exist" not in result.stdout


def test_usage_filter_reaches_awx(fake_aap: Any) -> None:
    _seed_usage_graph(fake_aap)
    # A server-side filter that excludes every node yields no usage rows.
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "usage",
            "--by-id",
            "10",
            "--filter",
            "workflow_job_template=999",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""


def test_usage_rejects_negative_depth(fake_aap: Any) -> None:
    result = CliInvoker().invoke(
        app,
        ["job-templates", "usage", "10", "--depth", "-1"],
    )
    assert result.exit_code != 0


def test_usage_help_advertises_org_alias_without_short_o() -> None:
    result = CliInvoker().invoke(app, ["job-templates", "usage", "--help"])
    assert result.exit_code == 0, result.output
    assert "--organization" in result.output
    assert "--org" in result.output
    assert re.search(r"(^|\s)-o(\s|,)", result.output) is None
