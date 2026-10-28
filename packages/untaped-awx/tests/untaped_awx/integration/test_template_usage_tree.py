"""``usage`` rows hold the whole ancestry, and ``--format tree`` draws them.

Seeded graph: job template 10 ``smoke-test`` runs twice in workflow 200
``nightly-backups``. 100 ``weekly-rollup`` runs ``nightly-backups``. 300
``quarterly-audit`` runs both ``nightly-backups`` and ``weekly-rollup`` (a
diamond). 500 ``annual-review`` runs ``quarterly-audit`` and job template 12
``cleanup``. Job template 11 ``unused`` runs nowhere.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from awx_test_support import run
from untaped.sdk import Terminal, tree_text
from untaped_awx.domain import WorkflowUsage
from untaped_awx.domain.workflow_usage import UsageTarget, usage_tree

_NODE_IDS = iter(range(1, 1000))


def _node(fake: Any, *, wf: tuple[int, str], ujt: tuple[int, str], ujt_type: str) -> None:
    fake.seed(
        "workflow_nodes",
        id=next(_NODE_IDS),
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
def ancestry(fake_aap: Any) -> Any:
    fake_aap.seed("organizations", id=1, name="Default")
    smoke = _template(fake_aap, "job_templates", 10, "smoke-test")
    _template(fake_aap, "job_templates", 11, "unused")
    cleanup = _template(fake_aap, "job_templates", 12, "cleanup")
    nightly = _template(fake_aap, "workflow_job_templates", 200, "nightly-backups")
    weekly = _template(fake_aap, "workflow_job_templates", 100, "weekly-rollup")
    quarterly = _template(fake_aap, "workflow_job_templates", 300, "quarterly-audit")
    annual = _template(fake_aap, "workflow_job_templates", 500, "annual-review")
    _node(fake_aap, wf=nightly, ujt=smoke, ujt_type="job")
    _node(fake_aap, wf=nightly, ujt=smoke, ujt_type="job")
    _node(fake_aap, wf=weekly, ujt=nightly, ujt_type="workflow_job")
    _node(fake_aap, wf=quarterly, ujt=nightly, ujt_type="workflow_job")
    _node(fake_aap, wf=quarterly, ujt=weekly, ujt_type="workflow_job")
    _node(fake_aap, wf=annual, ujt=quarterly, ujt_type="workflow_job")
    _node(fake_aap, wf=annual, ujt=cleanup, ujt_type="job")
    return fake_aap


def _run(*args: str, input: str | None = None) -> Any:
    return run(list(args), input=input)


def _link(id_: int, type_: str = "workflow_job_template", count: int = 1) -> dict[str, Any]:
    return {"id": id_, "type": type_, "node_count": count}


_ROWS = [
    {"id": 200, "name": "nightly-backups", "depth": 0, "node_count": 2,
     "contains": [_link(10, "job_template", 2)], "target": 10, "stopped": None},
    {"id": 100, "name": "weekly-rollup", "depth": 1, "node_count": 1,
     "contains": [_link(200)], "target": 10, "stopped": None},
    {"id": 300, "name": "quarterly-audit", "depth": 1, "node_count": 1,
     "contains": [_link(200), _link(100)], "target": 10, "stopped": None},
    {"id": 500, "name": "annual-review", "depth": 2, "node_count": 1,
     "contains": [_link(300)], "target": 10, "stopped": None},
]  # fmt: skip

_WHOLE_TREE = [
    "smoke-test  job template · used by",
    "└── nightly-backups (2 nodes)",
    "    ├── quarterly-audit [1]",
    "    │   └── annual-review",
    "    └── weekly-rollup",
    "        └── quarterly-audit",
    "            └── … see [1]",
    "",
]


def test_recursive_rows_hold_the_whole_ancestry_graph(ancestry: Any) -> None:
    result = _run("job-templates", "usage", "smoke-test", "--recursive", "-f", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _ROWS


def test_without_recursive_one_row_says_its_containers_were_not_followed(ancestry: Any) -> None:
    result = _run("job-templates", "usage", "smoke-test", "-f", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{**_ROWS[0], "stopped": "depth"}]


def test_filter_applies_to_every_lookup(ancestry: Any) -> None:
    result = _run(
        "job-templates", "usage", "smoke-test", "--recursive", "-f", "json",
        "--filter", "workflow_job_template__in=200,100",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.stdout)] == [200, 100]


def test_the_table_keeps_its_default_columns(ancestry: Any) -> None:
    # raw prints the first column; an edit of the default columns prints them
    result = _run("job-templates", "usage", "smoke-test", "--recursive", "-f", "raw", "-c", "+id")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "200\tnightly-backups\t0\t2",
        "100\tweekly-rollup\t1\t1",
        "300\tquarterly-audit\t1\t1",
        "500\tannual-review\t2\t1",
    ]


def test_the_tree_draws_the_whole_ancestry(ancestry: Any) -> None:
    result = _run("job-templates", "usage", "smoke-test", "--recursive", "-f", "tree")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [*_WHOLE_TREE, "4 workflows · top-level: annual-review"]
    assert result.stderr == ""


def test_with_filter_the_tree_marks_no_top_level_workflow(
    ancestry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    args = ["job-templates", "usage", "smoke-test", "--recursive", "-f", "tree"]

    marked = _run(*args).stdout
    filtered = _run(*args, "--filter", "workflow_job_template__gt=0").stdout

    assert "\x1b[1;32mannual-review\x1b[0m" in marked  # top-level: bold green
    assert "\x1b[1;32m" not in filtered
    assert "top-level" not in filtered
    assert "4 workflows" in filtered


def test_the_tree_is_ascii_under_an_ascii_border(
    ancestry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_UNTAPED__UI__BORDER", "ascii")

    result = _run("job-templates", "usage", "smoke-test", "--recursive", "-f", "tree")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[:7] == [
        "smoke-test  job template · used by",
        "`-- nightly-backups (2 nodes)",
        "    |-- quarterly-audit [1]",
        "    |   `-- annual-review",
        "    `-- weekly-rollup",
        "        `-- quarterly-audit",
        "            `-- ... see [1]",
    ]


@pytest.mark.parametrize("recursive", [[], ["--recursive"], ["--depth", "1"]])
def test_the_tree_draws_exactly_the_json_rows(ancestry: Any, recursive: list[str]) -> None:
    args = ["job-templates", "usage", "smoke-test", *recursive]
    rows = json.loads(_run(*args, "-f", "json").stdout)
    drawn = _run(*args, "-f", "tree").stdout

    tree = usage_tree(
        UsageTarget(id=10, name="smoke-test", type="job_template"),
        [WorkflowUsage.model_validate(row) for row in rows],
        top_level=True,
    )
    assert tree_text(tree, ui=Terminal()) + "\n" == drawn


def test_the_tree_alone_walks_direct_containers_and_hints_at_recursive(ancestry: Any) -> None:
    result = _run("job-templates", "usage", "smoke-test", "-f", "tree")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "smoke-test  job template · used by",
        "└── nightly-backups (2 nodes) …",
        "",
        "1 workflow · 1 stopped",
    ]
    assert result.stderr == (
        "hint: pass --recursive to walk up to the top-level workflows (1 stopped)\n"
    )


def test_a_depth_limit_draws_where_it_stopped(ancestry: Any) -> None:
    result = _run("job-templates", "usage", "smoke-test", "--depth", "1", "-f", "tree")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "smoke-test  job template · used by",
        "└── nightly-backups (2 nodes)",
        "    ├── quarterly-audit …",
        "    └── weekly-rollup …",
        "",
        "3 workflows · 2 stopped",
    ]
    assert result.stderr == (
        "hint: 2 workflows not read beyond --depth 1; raise --depth to read them\n"
    )


@pytest.mark.parametrize("fmt", ["json", "table"])
def test_other_formats_print_no_hint(ancestry: Any, fmt: str) -> None:
    result = _run("job-templates", "usage", "smoke-test", "-f", fmt)

    assert result.exit_code == 0, result.output
    assert result.stderr == ""


def test_a_cycle_through_the_target_draws_the_root_again_and_warns_once(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    alpha = _template(fake_aap, "workflow_job_templates", 100, "alpha")
    beta = _template(fake_aap, "workflow_job_templates", 200, "beta")
    _node(fake_aap, wf=alpha, ujt=beta, ujt_type="workflow_job")
    _node(fake_aap, wf=beta, ujt=alpha, ujt_type="workflow_job")
    args = ["workflow-templates", "usage", "beta", "--recursive"]

    tree = _run(*args, "-f", "tree")
    rows = json.loads(_run(*args, "-f", "json").stdout)

    assert tree.exit_code == 0, tree.output
    assert tree.stdout.splitlines() == [
        "beta  workflow template · used by",
        "└── alpha",
        "    └── beta ↻ cycle",
        "",
        "1 workflow",
    ]
    assert (
        tree.stderr == "warning: workflow 200 runs workflow 100, which already runs it (a cycle)\n"
    )
    assert [(row["id"], row["depth"]) for row in rows] == [(100, 0), (200, 1)]


def test_mutual_containment_along_two_paths_warns(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    deploy = _template(fake_aap, "job_templates", 20, "deploy")
    blue = _template(fake_aap, "workflow_job_templates", 600, "blue")
    green = _template(fake_aap, "workflow_job_templates", 700, "green")
    _node(fake_aap, wf=blue, ujt=deploy, ujt_type="job")
    _node(fake_aap, wf=green, ujt=deploy, ujt_type="job")
    _node(fake_aap, wf=blue, ujt=green, ujt_type="workflow_job")
    _node(fake_aap, wf=green, ujt=blue, ujt_type="workflow_job")

    result = _run("job-templates", "usage", "deploy", "--recursive", "-f", "tree")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "deploy  job template · used by",
        "├── blue",
        "│   └── green [1]",
        "│       └── blue ↻ cycle",
        "└── green",
        "    └── … see [1]",
        "",
        "2 workflows",
    ]
    assert (
        result.stderr
        == "warning: workflow 600 runs workflow 700, which already runs it (a cycle)\n"
    )


def test_two_workflows_sharing_a_name_get_their_ids_and_sort_by_id(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    migrate = _template(fake_aap, "job_templates", 30, "migrate")
    _node(fake_aap, wf=(1000, "deploy"), ujt=migrate, ujt_type="job")
    _node(fake_aap, wf=(800, "deploy"), ujt=migrate, ujt_type="job")
    # A workflow named like the job template needs no id: the first line tells them apart.
    _node(fake_aap, wf=(900, "migrate"), ujt=migrate, ujt_type="job")

    result = _run("job-templates", "usage", "migrate", "--recursive", "-f", "tree")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "migrate  job template · used by",
        "├── deploy #800",
        "├── deploy #1000",
        "└── migrate",
        "",
        "3 workflows · top-level: deploy #800, deploy #1000, migrate",
    ]


def test_a_workflow_running_itself_is_used_by_itself(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    loop = _template(fake_aap, "workflow_job_templates", 200, "loop")
    _node(fake_aap, wf=loop, ujt=loop, ujt_type="workflow_job")

    result = _run("workflow-templates", "usage", "loop", "-f", "tree")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["loop  workflow template · used by", "└── loop ↻ cycle"]
    assert result.stderr == "warning: workflow 200 runs itself (a cycle)\n"


def test_each_target_gets_its_tree_and_a_failed_one_doesnt_stop_the_others(
    ancestry: Any,
) -> None:
    result = _run("job-templates", "usage", "smoke-test", "missing", "cleanup", "-f", "tree")

    assert result.exit_code == 1
    assert result.stdout.splitlines() == [
        "smoke-test  job template · used by",
        "└── nightly-backups (2 nodes) …",
        "",
        "1 workflow · 1 stopped",
        "",
        "cleanup  job template · used by",
        "└── annual-review …",
        "",
        "1 workflow · 1 stopped",
    ]
    assert result.stderr.splitlines() == [
        "error: missing: JobTemplate not found: 'missing'",
        "hint: pass --recursive to walk up to the top-level workflows (2 stopped)",
    ]


def test_piped_records_get_one_tree_each(ancestry: Any) -> None:
    piped = "".join(
        json.dumps({"untaped": "1", "kind": "awx.job_template", "record": {"id": id_, "name": "x"}})
        + "\n"
        for id_ in (12, 11)
    )

    result = _run("job-templates", "usage", "--stdin", "-f", "tree", input=piped)

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "cleanup  job template · used by",
        "└── annual-review …",
        "",
        "1 workflow · 1 stopped",
        "",
        "unused  job template · in no workflow",
    ]


def test_an_unknown_id_is_not_found(ancestry: Any) -> None:
    result = _run("job-templates", "usage", "--by-id", "999", "-f", "tree")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "error: 999: JobTemplate not found (id=999)" in result.stderr


def test_an_unused_template_is_in_no_workflow(ancestry: Any) -> None:
    result = _run("job-templates", "usage", "unused", "-f", "tree")

    assert result.exit_code == 0, result.output
    assert result.stdout == "unused  job template · in no workflow\n"


@pytest.mark.parametrize("columns", ["id", "?"])
def test_columns_do_not_apply_to_the_tree(ancestry: Any, columns: str) -> None:
    result = _run("job-templates", "usage", "smoke-test", "-f", "tree", "-c", columns)

    assert result.exit_code == 2
    assert "--columns does not apply to --format tree" in result.stderr


def test_untaped_format_still_sets_the_default(
    ancestry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "json")

    result = run(["job-templates", "usage", "smoke-test"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["id"] == 200
