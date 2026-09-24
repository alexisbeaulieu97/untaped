"""End-to-end CLI tests for ``untaped awx workflow-templates nodes``.

Seeded tree: workflow 100 ``weekly-rollup`` holds node 1 (job template 10)
and node 2 (sub-workflow 200 ``nightly-backups``), which holds nodes 3 and 4
(job templates 11 and 12).
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration

_UJT_TYPES = {10: "job", 11: "job", 12: "job", 100: "workflow_job", 200: "workflow_job"}
_NAMES = {10: "smoke-test", 11: "db-backup", 12: "fs-backup", 100: "alpha", 200: "beta"}


def _node(fake: Any, id_: int, *, parent: int, parent_name: str, ujt: int, name: str) -> None:
    fake.seed(
        "workflow_nodes",
        id=id_,
        identifier=f"node-{id_}",
        workflow_job_template=parent,
        unified_job_template=ujt,
        summary_fields={
            "unified_job_template": {"id": ujt, "name": name, "unified_job_type": _UJT_TYPES[ujt]},
            "workflow_job_template": {"id": parent, "name": parent_name},
        },
    )


def _workflow(fake: Any, id_: int, name: str, org: int = 1, org_name: str = "Default") -> None:
    fake.seed(
        "workflow_job_templates", id=id_, name=name, organization=org, organization_name=org_name
    )


@pytest.fixture
def tree(fake_aap: Any) -> Any:
    fake_aap.seed("organizations", id=1, name="Default")
    _workflow(fake_aap, 100, "weekly-rollup")
    _workflow(fake_aap, 200, "nightly-backups")
    for jt in (10, 11, 12):
        fake_aap.seed(
            "job_templates", id=jt, name=_NAMES[jt], organization=1, organization_name="Default"
        )
    _node(fake_aap, 1, parent=100, parent_name="weekly-rollup", ujt=10, name="smoke-test")
    _node(fake_aap, 2, parent=100, parent_name="weekly-rollup", ujt=200, name="nightly-backups")
    _node(fake_aap, 3, parent=200, parent_name="nightly-backups", ujt=11, name="db-backup")
    _node(fake_aap, 4, parent=200, parent_name="nightly-backups", ujt=12, name="fs-backup")
    return fake_aap


def _nodes(*args: str, input: str | None = None) -> Any:
    return CliInvoker().invoke(app, ["workflow-templates", "nodes", *args], input=input)


def _ids(*args: str, input: str | None = None) -> list[int]:
    result = _nodes(*args, "--format", "raw", "--columns", "id", input=input)
    assert result.exit_code == 0, result.output
    return [int(line) for line in result.stdout.split()]


def test_nodes_repeatable_columns_contract(tree: Any) -> None:
    """Repeated ``--columns`` flags select the column set in order."""
    columns = ["id", "identifier", "name", "type", "depth"]
    result = _nodes(
        "--by-id", "100", "--format", "raw", *(f"--columns={column}" for column in columns)
    )
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.strip().splitlines()) == [
        "1\tnode-1\tsmoke-test\tjob_template\t0",
        "2\tnode-2\tnightly-backups\tworkflow_job_template\t0",
    ]


def test_nodes_recursive_rows_carry_depth_and_their_immediate_parent(tree: Any) -> None:
    result = _nodes(
        "--by-id",
        "100",
        "--recursive",
        "--format",
        "raw",
        "--columns",
        "id",
        "--columns",
        "depth",
        "--columns",
        "summary_fields.workflow_job_template.name",
    )
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.strip().splitlines()) == [
        "1\t0\tweekly-rollup",
        "2\t0\tweekly-rollup",
        "3\t1\tnightly-backups",
        "4\t1\tnightly-backups",
    ]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--by-id", "100"], [1, 2]),
        (["weekly-rollup"], [1, 2]),
        (["--by-id", "100", "--recursive"], [1, 2, 3, 4]),
        (["--by-id", "100", "--recursive", "--depth", "0"], [1, 2]),
        (["--by-id", "100", "--depth", "1"], [1, 2, 3, 4]),
        # --type filters the output, not the traversal
        (["--by-id", "100", "--recursive", "--type", "job_template"], [1, 3, 4]),
        (["--by-id", "100", "--recursive", "--type", "workflow_job_template"], [2]),
        (["--by-id", "100", "--filter", "unified_job_template=10"], [1]),
        # repeated filters compose server-side (AND)
        (["--by-id", "100", "--filter", "unified_job_template__in=10,200",
          "--filter", "unified_job_template__gt=11"], [2]),
        # the filter applies at every level of the recursion ...
        (["--by-id", "100", "--recursive", "--filter",
          "unified_job_template__in=10,11,12,200"], [1, 2, 3, 4]),
        # ... so excluding a sub-workflow row prunes its descent
        (["--by-id", "100", "--recursive", "--filter", "unified_job_template__in=10,11"], [1]),
        (["--by-id", "100", "--depth", "1", "--filter",
          "unified_job_template__in=10,11,200"], [1, 2, 3]),
    ],
)  # fmt: skip
def test_nodes_selection(tree: Any, args: list[str], expected: list[int]) -> None:
    assert sorted(_ids(*args)) == expected


def test_nodes_stdin_recursive_type_filter_end_to_end(tree: Any) -> None:
    ids = _ids("--stdin", "--by-id", "--recursive", "--type", "job_template", input="100\n")
    assert sorted(ids) == [1, 3, 4]


@pytest.mark.parametrize(
    ("name", "org_id", "org_name"),
    [
        # a numeric identifier is a name unless --by-id is given
        ("123", 1, "Default"),
        # --org scopes a name shared across organizations
        ("weekly-rollup", 2, "Other"),
    ],
)
def test_nodes_resolves_names_within_organization(
    tree: Any, name: str, org_id: int, org_name: str
) -> None:
    if org_id != 1:
        tree.seed("organizations", id=org_id, name=org_name)
    _workflow(tree, 300, name, org_id, org_name)
    _node(tree, 5, parent=300, parent_name=name, ujt=10, name="smoke-test")
    assert _ids(name, "--org", org_name) == [5]


@pytest.mark.parametrize(
    ("args", "input"),
    [(["--by-id", "100", "200"], None), (["--stdin", "--by-id"], "100\n200\n")],
)
def test_nodes_multiple_roots_are_listed_in_input_order(
    tree: Any, args: list[str], input: str | None
) -> None:
    ids = _ids(*args, input=input)
    assert sorted(ids) == [1, 2, 3, 4]
    # every root-100 row precedes every root-200 row; within a root, any order
    assert {*ids[:2]} == {1, 2}


@pytest.mark.parametrize(
    ("args", "input"),
    [
        (["--by-id", "100", "does-not-exist"], None),
        (["--stdin", "--by-id"], "100\ndoes-not-exist\n"),
    ],
)  # fmt: skip
def test_nodes_partial_failure_warns_and_exits_nonzero(
    tree: Any, args: list[str], input: str | None
) -> None:
    result = _nodes(*args, "--format", "raw", "--columns", "id", input=input)
    assert result.exit_code == 1, result.output
    assert sorted(result.stdout.split()) == ["1", "2"]
    assert "does-not-exist" in result.stderr
    assert "does-not-exist" not in result.stdout


@pytest.mark.parametrize(
    ("args", "input", "stderr"),
    [
        (["does-not-exist"], None, ""),
        (["100", "--depth", "-1"], None, ""),
        # --type is a Literal: a typo fails at parse time instead of matching nothing
        (["100", "--type", "job-template"], None, ""),
        (["100", "--filter", "no-equals-sign"], None, "--filter"),
        (["100", "--stdin"], "200\n", "stdin"),
        (["--stdin"], "", "stdin"),
    ],
)
def test_nodes_rejects_bad_input(
    tree: Any, args: list[str], input: str | None, stderr: str
) -> None:
    result = _nodes(*args, input=input)
    assert result.exit_code != 0
    assert stderr in result.stderr


def test_nodes_cycle_emits_stderr_warning(fake_aap: Any) -> None:
    """A → B → A: the re-entry is skipped with a warning on stderr, never stdout."""
    fake_aap.seed("organizations", id=1, name="Default")
    _workflow(fake_aap, 100, "alpha")
    _workflow(fake_aap, 200, "beta")
    _node(fake_aap, 1, parent=100, parent_name="alpha", ujt=200, name="beta")
    _node(fake_aap, 2, parent=200, parent_name="beta", ujt=100, name="alpha")
    result = _nodes("--by-id", "100", "--recursive", "--format", "raw", "--columns", "id")
    assert result.exit_code == 0, result.output
    assert "cycle" in result.stderr
    assert "100" in result.stderr
    assert "cycle" not in result.stdout


def test_nodes_help_advertises_org_alias_without_short_o() -> None:
    result = CliInvoker().invoke(app, ["workflow-templates", "nodes", "--help"])
    assert result.exit_code == 0, result.output
    assert "--organization" in result.output
    assert "--org" in result.output
    assert re.search(r"(^|\s)-o(\s|,)", result.output) is None
