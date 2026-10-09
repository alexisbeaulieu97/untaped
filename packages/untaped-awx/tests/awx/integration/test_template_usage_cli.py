"""End-to-end CLI tests for ``untaped awx job-templates/workflow-templates usage``.

Seeded graph: job template 10 ``smoke-test`` runs twice inside workflow 200
``nightly-backups``, which runs inside 100 ``weekly-rollup``, which runs
inside 300 ``quarterly-audit``. Job template 11 ``unused`` runs nowhere.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from untaped.testing import CliInvoker
from untaped_awx.cli import app


def _node(fake: Any, id_: int, *, wf: tuple[int, str], ujt: tuple[int, str], ujt_type: str) -> None:
    fake.seed(
        "workflow_nodes",
        id=id_,
        workflow_job_template=wf[0],
        unified_job_template=ujt[0],
        summary_fields={
            "unified_job_template": {"id": ujt[0], "name": ujt[1], "unified_job_type": ujt_type},
            "workflow_job_template": {"id": wf[0], "name": wf[1]},
        },
    )


def _template(fake: Any, collection: str, id_: int, name: str) -> tuple[int, str]:
    fake.seed(collection, id=id_, name=name, organization=1, organization_name="Default")
    return id_, name


@pytest.fixture
def graph(fake_aap: Any) -> Any:
    fake_aap.seed("organizations", id=1, name="Default")
    smoke = _template(fake_aap, "job_templates", 10, "smoke-test")
    _template(fake_aap, "job_templates", 11, "unused")
    nightly = _template(fake_aap, "workflow_job_templates", 200, "nightly-backups")
    weekly = _template(fake_aap, "workflow_job_templates", 100, "weekly-rollup")
    quarterly = _template(fake_aap, "workflow_job_templates", 300, "quarterly-audit")
    _node(fake_aap, 1, wf=nightly, ujt=smoke, ujt_type="job")
    _node(fake_aap, 2, wf=nightly, ujt=smoke, ujt_type="job")
    _node(fake_aap, 3, wf=weekly, ujt=nightly, ujt_type="workflow_job")
    _node(fake_aap, 4, wf=quarterly, ujt=weekly, ujt_type="workflow_job")
    return fake_aap


def _usage(*args: str, input: str | None = None) -> Any:
    return CliInvoker().invoke(app, [*args, "--format", "raw"], input=input)


def test_usage_repeatable_columns_contract(graph: Any) -> None:
    """Repeated ``--columns`` select columns in order; references in one workflow collapse."""
    result = _usage(
        "job-templates", "usage", "--by-id", "10",
        "--columns", "id", "--columns", "name", "--columns", "depth", "--columns", "node_count",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["200\tnightly-backups\t0\t2"]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["job-templates", "usage", "--by-id", "10"], ["200\t0"]),
        (["job-templates", "usage", "smoke-test"], ["200\t0"]),
        (["workflow-templates", "usage", "nightly-backups"], ["100\t0"]),
        (["job-templates", "usage", "--by-id", "10", "--recursive"],
         ["200\t0", "100\t1", "300\t2"]),
        (["job-templates", "usage", "--by-id", "10", "--depth", "1"], ["200\t0", "100\t1"]),
        (["job-templates", "usage", "unused"], []),
        # dedup is per root: a template queried twice emits its parent twice
        (["job-templates", "usage", "--by-id", "10", "10"], ["200\t0", "200\t0"]),
        (["job-templates", "usage", "--by-id", "10", "--filter", "workflow_job_template=999"], []),
    ],
)  # fmt: skip
def test_usage_walks_ancestry(graph: Any, args: list[str], expected: list[str]) -> None:
    result = _usage(*args, "--columns", "id", "--columns", "depth")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == expected


def test_usage_stdin_reads_roots_and_partial_failure_exits_nonzero(graph: Any) -> None:
    result = _usage(
        "job-templates", "usage", "--stdin", "--columns", "id",
        input="smoke-test\ndoes-not-exist\n",
    )  # fmt: skip
    assert result.exit_code == 1, result.output
    assert result.stdout.strip().splitlines() == ["200"]
    assert "does-not-exist" in result.stderr
    assert "does-not-exist" not in result.stdout


def _records(kind: str | None, *records: dict[str, Any]) -> str:
    return "".join(
        json.dumps({"untaped": "1", **({"kind": kind} if kind else {}), "record": record}) + "\n"
        for record in records
    )


def test_usage_stdin_typed_records_use_their_id_not_their_name(graph: Any) -> None:
    """A piped record names its template by ``id``: a name shared across orgs can't misroute it."""
    graph.seed("organizations", id=2, name="Other")
    graph.seed("job_templates", id=12, name="smoke-test", organization=2, organization_name="Other")
    piped = _records(
        "awx.job_template", {"id": 12, "name": "smoke-test"}, {"id": 10, "name": "renamed"}
    )
    result = _usage("job-templates", "usage", "--stdin", "--columns", "id", input=piped)
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["200"]


@pytest.mark.parametrize(
    ("args", "input", "expected"),
    [
        (["job-templates", "usage", "--stdin"], "smoke-test\n", "200"),
        (["job-templates", "usage", "--stdin", "--by-id"], "10\n", "200"),
        # --by-id is moot for records: they always carry their id
        (["job-templates", "usage", "--stdin", "--by-id"],
         _records("awx.job_template", {"id": 10, "name": "smoke-test"}), "200"),
        # a record without a kind is read by its id too
        (["job-templates", "usage", "--stdin"], _records(None, {"id": 10}), "200"),
        (["workflow-templates", "usage", "--stdin"],
         _records("awx.workflow_job_template", {"id": 200}), "100"),
    ],
)  # fmt: skip
def test_usage_stdin_bare_lines_and_records(
    graph: Any, args: list[str], input: str, expected: str
) -> None:
    result = _usage(*args, "--columns", "id", input=input)
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == [expected]


@pytest.mark.parametrize(
    ("input", "exit_code", "stderr"),
    [
        # one stream is records or bare lines, never both
        ("smoke-test\n" + _records("awx.job_template", {"id": 10}), 1, "mixed"),
        (_records("awx.job_template", {"id": 10}) + "smoke-test\n", 1, "line 2"),
        # a record is never re-resolved by name, with or without a kind
        (_records("awx.job_template", {"name": "smoke-test"}), 1, "id"),
        (_records(None, {"name": "smoke-test"}), 1, "id"),
        (_records("awx.job_template", {"id": "10"}), 1, "id"),
        (_records("awx.workflow_job_template", {"id": 200}), 2, "awx.workflow_job_template"),
    ],
)  # fmt: skip
def test_usage_stdin_rejects_bad_records(
    graph: Any, input: str, exit_code: int, stderr: str
) -> None:
    result = _usage("job-templates", "usage", "--stdin", input=input)
    assert result.exit_code == exit_code, result.output
    assert stderr in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("args", [["does-not-exist"], ["10", "--depth", "-1"]])
def test_usage_rejects_bad_input(graph: Any, args: list[str]) -> None:
    assert _usage("job-templates", "usage", *args).exit_code != 0


def test_usage_cycle_emits_stderr_warning(fake_aap: Any) -> None:
    """A (100) contains B (200) and B contains A: warn on stderr, keep stdout clean."""
    fake_aap.seed("organizations", id=1, name="Default")
    alpha = _template(fake_aap, "workflow_job_templates", 100, "alpha")
    beta = _template(fake_aap, "workflow_job_templates", 200, "beta")
    _node(fake_aap, 1, wf=alpha, ujt=beta, ujt_type="workflow_job")
    _node(fake_aap, 2, wf=beta, ujt=alpha, ujt_type="workflow_job")
    result = _usage(
        "workflow-templates", "usage", "--by-id", "200", "--recursive", "--columns", "id"
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["100"]
    assert "cycle" in result.stderr
    assert "cycle" not in result.stdout


def test_usage_help_advertises_org_alias_without_short_o() -> None:
    result = CliInvoker().invoke(app, ["job-templates", "usage", "--help"])
    assert result.exit_code == 0, result.output
    assert "--organization" in result.output
    assert "--org" in result.output
    assert re.search(r"(^|\s)-o(\s|,)", result.output) is None
