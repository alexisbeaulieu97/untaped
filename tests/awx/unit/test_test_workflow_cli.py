"""End-to-end ``awx test`` runs of workflow suites against the fake AAP's workflow runs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from untaped.capabilities.awx.cli import app
from untaped.capabilities.awx.cli.context import AwxContext
from untaped.capabilities.awx.domain.workflow_run import MAX_NESTING
from untaped.testing import CliInvoker

if TYPE_CHECKING:  # pragma: no cover — pytest --import-mode=importlib hides 'tests'
    from tests.conftest import FakeAap
else:
    FakeAap = object  # type: ignore[assignment,misc]


@pytest.fixture
def cli() -> CliInvoker:
    return CliInvoker()


@pytest.fixture
def aap(fake_aap: FakeAap, monkeypatch: pytest.MonkeyPatch) -> FakeAap:
    """The fake AAP; polling a workflow never sleeps."""
    monkeypatch.setattr(AwxContext, "pause", lambda self, seconds: None)
    fake_aap.seed("organizations", id=1, name="Default")
    return fake_aap


def _seed_release(
    fake: FakeAap, *, approval: bool = False, rollback: bool = True, notify: bool = False
) -> dict[str, int]:
    """``Release``: build → [approve →] deploy → verify, and rollback when deploy fails.

    Without ``rollback``, the rollback node's template was deleted. With
    ``notify``, a node runs once both build and deploy succeeded.
    """
    templates = {
        name: fake.seed("job_templates", name=name, organization=1, project=5)["id"]
        for name in ("Build", "Deploy", "Smoke check", "Rollback")
    }
    fake.seed("projects", id=5, name="Playbooks", scm_revision="abc123")
    workflow = fake.seed(
        "workflow_job_templates",
        name="Release",
        organization=1,
        ask_variables_on_launch=True,
        ask_scm_branch_on_launch=True,
    )["id"]
    names = ["build", "approve", "deploy", "verify", "rollback", "notify"]
    node_ids = {name: 900 + index for index, name in enumerate(names)}

    def node(identifier: str, template: int | None, **edges: Any) -> None:
        converge = edges.pop("converge", False)
        fake.seed(
            "workflow_nodes",
            id=node_ids[identifier],
            workflow_job_template=workflow,
            identifier=identifier,
            unified_job_template=template,
            all_parents_must_converge=converge,
            **{
                f"{edge}_nodes": [node_ids[child] for child in children]
                for edge, children in edges.items()
            },
        )

    after_build = ["approve" if approval else "deploy", *(["notify"] if notify else [])]
    node("build", templates["Build"], success=after_build)
    if approval:
        gate = fake.seed("workflow_approval_templates", name="Approve production", timeout=0)
        node("approve", gate["id"], success=["deploy"])
    after_deploy = ["verify", *(["notify"] if notify else [])]
    node("deploy", templates["Deploy"], success=after_deploy, failure=["rollback"])
    node("verify", templates["Smoke check"])
    node("rollback", templates["Rollback"] if rollback else None)
    if notify:
        node("notify", templates["Smoke check"], converge=True)
    return {"workflow": workflow, **templates}


def _suite(tmp_path: Path, cases: dict[str, Any], **body: Any) -> Path:
    document = {"kind": "AwxTestSuite", "name": "release", "workflowTemplate": "Release"}
    path = tmp_path / "release.yml"
    path.write_text(yaml.safe_dump({**document, **body, "cases": cases}))
    return path


def _run(cli: CliInvoker, suite: Path, *args: str) -> tuple[int, list[dict[str, Any]], str]:
    result = cli.invoke(app, ["test", "run", str(suite), "--parallel", "1", "-f", "json", *args])
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    return result.exit_code, rows, result.stderr


def _failed_task(host: str, task: str, msg: str) -> dict[str, Any]:
    return {
        "event": "runner_on_failed",
        "failed": True,
        "host_name": host,
        "task": task,
        "event_data": {"res": {"msg": msg}},
    }


def _paths(fake: FakeAap) -> list[str]:
    return [call.request.url.path.removeprefix("/api/v2/") for call in fake.router.calls]


def test_a_workflow_case_passes_and_lists_its_nodes(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["deploy"] = {"host_summaries": [{"host_name": "web1", "changed": 1}]}
    suite = _suite(
        tmp_path,
        {
            "happy": {
                "expect": {
                    "nodes": {
                        "deploy": {"hosts": {"*": {"failed": 0}}, "changed": 1},
                        "rollback": {"status": "never_ran"},
                    }
                }
            }
        },
    )

    code, [row], stderr = _run(cli, suite)

    assert code == 0, stderr
    assert row["result"] == "pass"
    assert row["job_status"] == "successful"
    assert aap.get_record("workflow_jobs", row["job_id"])["status"] == "successful"
    nodes = {node["id"]: node for node in row["nodes"]}
    assert [node["id"] for node in row["nodes"]] == ["build", "deploy", "verify", "rollback"]
    assert nodes["deploy"]["template"] == "Deploy"
    assert nodes["deploy"]["status"] == "successful"
    assert nodes["rollback"] == {
        "id": "rollback",
        "template": "Rollback",
        "job_id": None,
        "status": "never_ran",
    }
    assert [(check["check"], check.get("node")) for check in row["expectations"]] == [
        ("status", None),
        ("status", "deploy"),
        ("changed", "deploy"),
        ("hosts", "deploy"),
        ("status", "rollback"),
    ]
    # Only the node a check needs is read: deploy's host summaries, no other node job.
    read_jobs = {path for path in _paths(aap) if path.startswith("jobs/")}
    assert read_jobs == {
        f"jobs/{nodes['deploy']['job_id']}/",
        f"jobs/{nodes['deploy']['job_id']}/job_host_summaries/",
    }


def test_a_failing_node_is_blamed_with_its_jobs_evidence(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["verify"] = {
        "status": "failed",
        "events": [_failed_task("web2", "Check health", "HTTP 503")],
        "host_summaries": [{"host_name": "web2", "failures": 1}],
    }
    suite = _suite(tmp_path, {"smoke": {}})

    code, [row], _ = _run(cli, suite)

    assert code == 1
    assert (row["result"], row["job_status"]) == ("fail", "failed")
    failure = row["failure"]
    assert (failure["system"], failure["category"]) == ("awx.playbook", "failed")
    assert failure["message"] == "node verify: task 'Check health' failed on web2: HTTP 503"
    evidence = failure["evidence"]
    assert evidence["node"] == "verify"
    assert [task["host"] for task in evidence["failed_tasks"]] == ["web2"]
    verify = next(node for node in row["nodes"] if node["id"] == "verify")
    assert verify["status"] == "failed"


def test_a_node_expectation_blames_a_node_that_failed_on_a_handled_path(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    """Deploy fails, rollback handles it: the workflow succeeds, the deploy node does not."""
    _seed_release(aap)
    aap.node_outcomes["deploy"] = {
        "status": "failed",
        "events": [_failed_task("web1", "Copy release", "disk full")],
    }
    suite = _suite(
        tmp_path,
        {
            "deploys": {"expect": {"nodes": {"deploy": {}}}},
            "rolls-back": {
                "expect": {
                    "nodes": {"deploy": {"status": "failed"}, "verify": {"status": "never_ran"}}
                }
            },
        },
    )

    code, [deploys, rolls_back], _ = _run(cli, suite)

    assert code == 1
    assert deploys["job_status"] == "successful"
    assert deploys["failure"]["system"] == "awx.playbook"
    assert deploys["failure"]["message"] == (
        "node deploy: task 'Copy release' failed on web1: disk full"
    )
    assert rolls_back["result"] == "pass"


def test_a_node_expected_never_to_run_fails_on_the_expectation(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["deploy"] = {"status": "failed"}
    suite = _suite(tmp_path, {"c": {"expect": {"nodes": {"rollback": {"status": "never_ran"}}}}})

    code, [row], _ = _run(cli, suite)

    assert code == 1
    assert row["failure"]["system"] == "awx.expectation"
    assert row["failure"]["message"] == "node rollback: expected status never_ran, got successful"


@pytest.mark.parametrize(
    ("approvals", "status", "decision"),
    [("approve", "successful", "approve"), ("deny", "failed", "deny")],
)
def test_approvals_are_answered_as_the_case_says(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path, approvals: str, status: str, decision: str
) -> None:
    _seed_release(aap, approval=True)
    never_ran = {"deploy": {"status": "never_ran"}} if decision == "deny" else {}
    suite = _suite(
        tmp_path,
        {"c": {"approvals": approvals, "expect": {"status": status, "nodes": never_ran}}},
    )

    code, [row], stderr = _run(cli, suite)

    assert code == 0, stderr
    [(_, approval_id, action, _)] = [
        call for call in aap.actions_called if call[0] == "workflow_approvals"
    ]
    assert action == decision
    approve = next(node for node in row["nodes"] if node["id"] == "approve")
    assert (approve["job_id"], approve["template"]) == (approval_id, "Approve production")


def test_a_denial_the_workflow_cannot_survive_fails_the_expectation(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap, approval=True)
    suite = _suite(tmp_path, {"c": {}}, defaults={"approvals": "deny"})

    code, [row], _ = _run(cli, suite)

    assert code == 1
    assert row["failure"]["system"] == "awx.expectation"
    assert row["failure"]["message"] == (
        "node approve: approval 'Approve production' was denied as the case asked "
        "(approvals: deny), and no failure path leads out of it; "
        "expected status successful, got failed"
    )


def test_a_pending_approval_without_approvals_fails_fast_and_cancels(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap, approval=True)
    suite = _suite(tmp_path, {"c": {}})

    code, [row], _ = _run(cli, suite)

    assert code == 1
    assert row["result"] == "error"
    failure = row["failure"]
    assert (failure["system"], failure["category"]) == ("awx.suite", "invalid")
    assert failure["message"].startswith(
        "node approve: approval 'Approve production' is waiting, and the case sets no approvals"
    )
    assert failure["message"].endswith("; cancel requested")
    assert "approvals: approve" in failure["hint"]
    assert ("workflow_jobs", row["job_id"], "cancel", {}) in aap.actions_called
    assert not [call for call in aap.actions_called if call[0] == "workflow_approvals"]


def test_an_idempotent_workflow_reruns_whole_and_sums_changes_over_its_nodes(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    changed = {
        "event": "runner_on_ok",
        "changed": True,
        "host_name": "web1",
        "task": "Write config",
    }
    aap.node_outcomes["build"] = {"host_summaries": [{"host_name": "web1", "changed": 1}]}
    aap.node_outcomes["deploy"] = [
        {"host_summaries": [{"host_name": "web1", "changed": 2}]},
        {"host_summaries": [{"host_name": "web1", "changed": 1}], "events": [changed]},
    ]
    suite = _suite(
        tmp_path,
        {"c": {"launch": {"scm_branch": "feature/x"}, "expect": {"idempotent": True}}},
    )

    code, [row], _ = _run(cli, suite)

    assert code == 1
    assert row["rerun_job_id"] not in (None, row["job_id"])
    assert row["failure"]["system"] == "awx.expectation"
    # build changed 1 on web1 in the rerun too: 1 + 1 over the rerun's node jobs
    assert row["failure"]["message"] == "not idempotent: the rerun ended successful, 2 changed"
    assert row["failure"]["evidence"]["changed_tasks"] == [{"host": "web1", "task": "Write config"}]
    launches = [body for path, _, action, body in aap.actions_called if action == "launch"]
    # The rerun runs the commit the first run's node jobs ran.
    assert [body.get("scm_branch") for body in launches] == ["feature/x", "abc123"]


def test_an_idempotent_workflow_that_changes_nothing_the_second_time_passes(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["deploy"] = [{"host_summaries": [{"host_name": "web1", "changed": 2}]}, {}]
    suite = _suite(tmp_path, {"c": {"expect": {"idempotent": True, "changed": 2}}})

    code, [row], stderr = _run(cli, suite)

    assert code == 0, stderr
    assert row["expectations"][-1]["actual"] == "successful, 0 changed"


def test_compare_treats_a_workflow_case_like_a_job_case(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["verify"] = {"status": "failed"}
    suite = _suite(tmp_path, {"c": {}})
    _, before, _ = _run(cli, suite)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(before))

    code, [row], _ = _run(cli, suite, "--compare", str(baseline))

    assert code == 0
    assert row["change"] == "still_failing"


def test_a_failure_inside_a_nested_workflow_names_the_path_to_it(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    ids = _seed_release(aap)
    outer = aap.seed("workflow_job_templates", name="Train", organization=1)["id"]
    aap.seed(
        "workflow_nodes",
        workflow_job_template=outer,
        identifier="release",
        unified_job_template=ids["workflow"],
    )
    aap.node_outcomes["verify"] = {
        "status": "failed",
        "events": [_failed_task("web2", "Check", "x")],
    }
    suite = _suite(tmp_path, {"c": {}}, workflowTemplate="Train")

    code, [row], _ = _run(cli, suite)

    assert code == 1
    assert row["failure"]["message"] == "node release/verify: task 'Check' failed on web2: x"
    assert row["failure"]["evidence"]["node"] == "release/verify"


def _seed_train(fake: FakeAap, *, approval: bool = False) -> None:
    """``Train``: one node, ``release``, that runs the ``Release`` workflow."""
    ids = _seed_release(fake, approval=approval)
    outer = fake.seed("workflow_job_templates", name="Train", organization=1)["id"]
    fake.seed(
        "workflow_nodes",
        workflow_job_template=outer,
        identifier="release",
        unified_job_template=ids["workflow"],
    )


def test_a_nested_workflow_node_is_checked_as_a_workflow(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_train(aap, approval=True)
    aap.node_outcomes["deploy"] = {"host_summaries": [{"host_name": "web1", "changed": 2}]}
    aap.node_outcomes["verify"] = {"host_summaries": [{"host_name": "web1", "changed": 1}]}
    suite = _suite(
        tmp_path,
        {
            "c": {
                "expect": {"changed": 3, "nodes": {"release": {"hosts": {"web1": {"changed": 3}}}}}
            }
        },
        workflowTemplate="Train",
        defaults={"approvals": "approve"},
    )

    code, [row], stderr = _run(cli, suite)

    assert code == 0, stderr
    assert row["hosts"]["web1"]["changed"] == 3
    assert [(check["check"], check.get("node")) for check in row["expectations"]] == [
        ("status", None),
        ("changed", None),
        ("status", "release"),
        ("hosts", "release"),
    ]
    # The approval inside the nested workflow was answered.
    assert [call[2] for call in aap.actions_called if call[0] == "workflow_approvals"] == [
        "approve"
    ]


def test_an_approval_node_has_only_a_status_to_check(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap, approval=True)
    suite = _suite(
        tmp_path,
        {
            "status": {"expect": {"nodes": {"approve": {"status": "successful"}}}},
            "hosts": {"expect": {"nodes": {"approve": {"changed": 0}}}},
        },
        defaults={"approvals": "approve"},
    )

    code, [hosts, status], _ = _run(cli, suite)

    assert code == 1
    assert status["result"] == "pass"
    assert (hosts["result"], hosts["failure"]["system"], hosts["failure"]["message"]) == (
        "error",
        "awx.suite",
        "node approve: a workflow_approval node has only a status to check",
    )


def test_a_negative_workflow_case_matches_the_failed_tasks_of_its_nodes(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["verify"] = {
        "status": "failed",
        "events": [_failed_task("web2", "Check health", "HTTP 503")],
        "host_summaries": [{"host_name": "web2", "failures": 1}],
    }
    expect = {"status": "failed", "failed_tasks": [{"task": "Check health", "msg": "503"}]}
    wrong = {"status": "failed", "failed_tasks": [{"task": "Migrate"}]}
    suite = _suite(tmp_path, {"right": {"expect": expect}, "wrong": {"expect": wrong}})

    code, [right, wrong_row], _ = _run(cli, suite)

    assert code == 1
    assert right["result"] == "pass"
    assert wrong_row["failure"]["system"] == "awx.expectation"
    assert wrong_row["failure"]["message"] == "no failed task matches task 'Migrate'"


def test_unreadable_nodes_make_the_case_an_error_never_a_node_that_never_ran(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.sub_path_errors[("workflow_jobs", "workflow_nodes")] = [502] * 10
    suite = _suite(tmp_path, {"c": {"expect": {"nodes": {"deploy": {}, "verify": {}}}}})

    code, [row], _ = _run(cli, suite)

    assert code == 5
    assert (row["result"], row["nodes"]) == ("error", None)
    failure = row["failure"]
    assert (failure["system"], failure["category"]) == ("awx.controller", "unavailable")
    assert failure["message"].startswith("workflow nodes unreadable: ")
    # No node is checked against nodes that could not be read.
    assert [check["check"] for check in row["expectations"]] == ["status"]


def test_a_failure_a_path_handled_does_not_explain_the_workflow(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    """Deploy fails and its rollback's template is gone: AWX fails the workflow for that."""
    _seed_release(aap, rollback=False)
    aap.node_outcomes["deploy"] = {"status": "failed"}

    code, [row], _ = _run(cli, _suite(tmp_path, {"c": {}}))

    assert code == 5
    assert row["failure"]["system"] == "awx.controller"
    assert row["failure"]["message"].startswith(
        "workflow job failed, but no failed node explains it: Workflow job node(s) missing "
        "unified job template and error handling path ["
    )


@pytest.mark.parametrize(
    ("held", "system"), [("running", "awx.playbook"), ("pending", "awx.controller")]
)
def test_a_workflow_timeout_is_blamed_on_the_node_still_running(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path, held: str, system: str
) -> None:
    _seed_release(aap)
    aap.node_outcomes["deploy"] = {"hold": 10_000, "hold_status": held}

    code, [row], _ = _run(cli, _suite(tmp_path, {"c": {}}), "--timeout", "0.01")

    assert row["result"] == "timeout"
    failure = row["failure"]
    assert failure["system"] == system
    assert failure["message"] == f"node deploy: still {held} after 0.01s; cancel requested"
    assert failure["evidence"]["node"] == "deploy"
    assert {node["id"]: node["status"] for node in row["nodes"]} == {
        "build": "successful",
        "deploy": held,
        "verify": "never_ran",
        "rollback": "never_ran",
    }
    assert ("workflow_jobs", row["job_id"], "cancel", {}) in aap.actions_called
    assert code == (1 if held == "running" else 5)


def test_a_node_waiting_for_all_its_parents_runs_only_when_all_succeed(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap, notify=True)
    aap.node_outcomes["deploy"] = [{}, {"status": "failed"}]
    suite = _suite(
        tmp_path,
        {
            "both": {"expect": {"nodes": {"notify": {"status": "successful"}}}},
            "one": {"expect": {"nodes": {"notify": {"status": "never_ran"}}}},
        },
    )

    code, rows, stderr = _run(cli, suite)

    assert code == 0, stderr
    assert [row["result"] for row in rows] == ["pass", "pass"]


@pytest.mark.parametrize(
    ("kind", "store", "system", "code"),
    [
        ("project", "projects", "awx.scm", 1),
        ("inventory_source", "inventory_sources", "awx.inventory", 4),
    ],
)
def test_a_node_that_runs_an_update_is_blamed_on_the_update(
    cli: CliInvoker,
    aap: FakeAap,
    tmp_path: Path,
    kind: str,
    store: str,
    system: str,
    code: int,
) -> None:
    source = aap.seed(store, name="Playbooks sync")["id"]
    workflow = aap.seed("workflow_job_templates", name="Sync", organization=1)["id"]
    aap.seed(
        "workflow_nodes",
        workflow_job_template=workflow,
        identifier="sync",
        unified_job_template=source,
    )
    aap.node_outcomes["sync"] = {
        "status": "failed",
        "events": [_failed_task("localhost", "Update source", "couldn't find remote ref")],
    }

    exit_code, [row], _ = _run(cli, _suite(tmp_path, {"c": {}}, workflowTemplate="Sync"))

    assert exit_code == code
    failure = row["failure"]
    assert failure["system"] == system
    assert failure["message"].startswith("node sync: ")
    assert failure["message"].endswith(" for 'Playbooks sync' failed: couldn't find remote ref")
    assert failure["evidence"]["node"] == "sync"
    assert failure["evidence"]["related"]["kind"] == f"{kind.split('_')[0]}_update"


def test_an_approval_that_timed_out_is_the_controllers(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap, approval=True)
    aap.node_outcomes["approve"] = {"timed_out": True}
    suite = _suite(tmp_path, {"c": {"approvals": "approve"}})

    code, [row], _ = _run(cli, suite)

    assert code == 5
    assert row["failure"]["system"] == "awx.controller"
    assert row["failure"]["message"].startswith(
        "node approve: approval 'Approve production' (workflow approval "
    )
    assert row["failure"]["message"].endswith(") was denied outside this run, or timed out")


def test_a_nested_pending_approval_names_its_full_path(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_train(aap, approval=True)

    code, [row], _ = _run(cli, _suite(tmp_path, {"c": {}}, workflowTemplate="Train"))

    assert code == 1
    assert row["failure"]["message"].startswith(
        "node release/approve: approval 'Approve production' is waiting"
    )
    assert row["failure"]["evidence"]["node"] == "release/approve"


def test_without_cancel_a_pending_approval_says_how_to_finish_the_workflow(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap, approval=True)

    code, [row], _ = _run(cli, _suite(tmp_path, {"c": {}}), "--no-cancel")

    assert code == 1
    [approval] = aap.list_records("workflow_approvals")
    assert row["failure"]["message"] == (
        "node approve: approval 'Approve production' is waiting, and the case sets no "
        f"approvals; it keeps running: approve or deny workflow approval {approval['id']} in "
        f"AWX, or cancel it with `untaped awx jobs cancel {row['job_id']} --kind workflow_job`"
    )
    assert not [call for call in aap.actions_called if call[2] == "cancel"]


def test_a_failed_workflow_rerun_names_the_workflow_job(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["verify"] = [{}, {"status": "failed"}]
    suite = _suite(tmp_path, {"c": {"expect": {"idempotent": True}}})

    code, [row], _ = _run(cli, suite)

    assert code == 1
    assert row["failure"]["message"].startswith(
        f"rerun workflow job {row['rerun_job_id']}: node verify: "
    )


def test_the_nodes_are_not_read_while_polling_a_workflow_without_approvals(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["deploy"] = {"hold": 3}

    code, [row], stderr = _run(cli, _suite(tmp_path, {"c": {}}))

    assert code == 0, stderr
    node_reads = [path for path in _paths(aap) if path.endswith("/workflow_nodes/")]
    assert node_reads.count(f"workflow_jobs/{row['job_id']}/workflow_nodes/") == 1


def test_a_transient_node_read_error_while_polling_is_retried(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap, approval=True)
    aap.sub_path_errors[("workflow_jobs", "workflow_nodes")] = [502]

    code, [row], stderr = _run(cli, _suite(tmp_path, {"c": {"approvals": "approve"}}))

    assert code == 0, stderr
    assert row["result"] == "pass"


def test_a_workflow_past_the_host_cut_is_checked_against_every_host(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    hosts = [{"host_name": f"web{index:03}"} for index in range(600)]
    aap.node_outcomes["deploy"] = {"host_summaries": [*hosts, {"host_name": "zz", "changed": 1}]}
    aap.node_outcomes["verify"] = {"host_summaries": [{"host_name": "zz", "changed": 1}]}
    suite = _suite(tmp_path, {"c": {"expect": {"hosts": {"*": {"changed": 1}}}}})

    code, [row], _ = _run(cli, suite)

    assert code == 1
    assert (len(row["hosts"]), row["hosts_truncated"]) == (500, True)
    [bound] = [check for check in row["expectations"] if check["check"] == "hosts"]
    assert (bound["passed"], bound["actual"]) == (False, "zz=2")


def test_a_culprit_node_that_is_also_checked_is_read_once(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["verify"] = {
        "status": "failed",
        "events": [_failed_task("web2", "Check health", "HTTP 503")],
    }
    suite = _suite(tmp_path, {"c": {"expect": {"nodes": {"verify": {}}}}})

    code, [row], _ = _run(cli, suite)

    assert code == 1
    verify = next(node for node in row["nodes"] if node["id"] == "verify")
    reads = Counter(
        (call.request.url.path, str(call.request.url.params))
        for call in aap.router.calls
        if f"/jobs/{verify['job_id']}/" in call.request.url.path
    )
    assert reads and max(reads.values()) == 1, reads


def test_a_node_job_that_cannot_be_read_is_the_controllers(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    aap.node_outcomes["verify"] = {"status": "failed"}
    real_action = aap._action

    def launch_then_lose_the_failed_job(*args: Any) -> Any:
        response = real_action(*args)
        for job_id, job in list(aap.store["jobs"].items()):
            if job["status"] == "failed":
                del aap.store["jobs"][job_id]
        return response

    aap._action = launch_then_lose_the_failed_job  # type: ignore[method-assign]
    suite = _suite(tmp_path, {"c": {"expect": {"nodes": {"verify": {"status": "failed"}}}}})

    code, [row], _ = _run(cli, suite)

    assert code == 1
    assert row["failure"]["system"] == "awx.controller"
    assert row["failure"]["message"].startswith("node verify: job ")
    assert "unreadable" in row["failure"]["message"]


def test_nested_workflows_are_followed_only_so_deep(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    """Level 0 runs level 1, … level MAX_NESTING + 1 runs a job that fails."""
    ids = _seed_release(aap)
    below = aap.seed("workflow_job_templates", name="Level 6", organization=1)["id"]
    aap.seed(
        "workflow_nodes",
        workflow_job_template=below,
        identifier="deploy",
        unified_job_template=ids["Deploy"],
    )
    for level in range(MAX_NESTING, -1, -1):
        above = aap.seed("workflow_job_templates", name=f"Level {level}", organization=1)["id"]
        aap.seed(
            "workflow_nodes",
            workflow_job_template=above,
            identifier=f"l{level + 1}",
            unified_job_template=below,
        )
        below = above
    aap.node_outcomes["deploy"] = {"status": "failed"}

    code, [row], _ = _run(cli, _suite(tmp_path, {"c": {}}, workflowTemplate="Level 0"))

    assert code == 1
    path = "/".join(f"l{level}" for level in range(1, MAX_NESTING + 2))
    assert row["failure"]["system"] == "awx.playbook"
    assert row["failure"]["evidence"]["node"] == path
    assert row["failure"]["message"].startswith(f"node {path}: workflow job ")
    assert row["failure"]["message"].endswith(
        f"ended failed; workflows nested more than {MAX_NESTING} deep are not followed"
    )


def test_validate_refuses_an_unknown_node_and_warns_about_unanswered_approvals(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap, approval=True)
    suite = _suite(
        tmp_path,
        {
            "typo": {"approvals": "approve", "expect": {"nodes": {"deplyo": {}}}},
            "silent": {},
        },
    )

    result = cli.invoke(app, ["test", "validate", str(suite)])

    assert result.exit_code == 1
    assert (
        "release/typo: workflow node not found: 'deplyo' in workflow 'Release'; "
        "did you mean 'deploy'?"
    ) in result.stderr
    assert (
        "warning: release/silent: the workflow has approval nodes (approve) and the case "
        "sets no approvals, so a pending approval fails it"
    ) in result.stderr


def test_validate_warns_about_approvals_in_a_nested_workflow(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_train(aap, approval=True)
    suite = _suite(
        tmp_path, {"silent": {}, "answered": {"approvals": "deny"}}, workflowTemplate="Train"
    )

    result = cli.invoke(app, ["test", "validate", str(suite)])

    assert result.exit_code == 0, result.output
    warnings = [line for line in result.stderr.splitlines() if line.startswith("warning:")]
    assert warnings == [
        "warning: release/silent: the workflow has approval nodes (release/approve) and the "
        "case sets no approvals, so a pending approval fails it"
    ]


def test_run_preflight_refuses_an_unknown_node_before_launching(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    _seed_release(aap)
    suite = _suite(tmp_path, {"c": {"expect": {"nodes": {"nope": {}}}}})

    code, rows, stderr = _run(cli, suite)

    assert (code, rows) == (1, [])
    assert "workflow node not found: 'nope'" in stderr
    assert not [call for call in aap.actions_called if call[2] == "launch"]


def test_a_job_template_and_a_workflow_of_one_name_are_preflighted_apart(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path
) -> None:
    """The job template's prompts never stand in for the workflow's of the same name."""
    _seed_release(aap)
    aap.seed("job_templates", name="Release", organization=1, ask_variables_on_launch=False)
    job = tmp_path / "a-job.yml"
    job.write_text(
        yaml.safe_dump(
            {"kind": "AwxTestSuite", "name": "job", "jobTemplate": "Release", "cases": {"c": {}}}
        )
    )
    workflow = _suite(tmp_path, {"c": {"launch": {"extra_vars": {"test": 1}}}})

    result = cli.invoke(
        app, ["test", "run", str(job), str(workflow), "--parallel", "1", "-f", "json"]
    )

    assert result.exit_code == 0, result.stderr
    assert sorted(row["result"] for row in json.loads(result.stdout)) == ["pass", "pass"]


def test_list_shows_the_workflow_template(cli: CliInvoker, tmp_path: Path) -> None:
    suite = _suite(tmp_path, {"c": {}})

    result = cli.invoke(app, ["test", "list", str(suite), "-f", "json"])

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert (row["job_template"], row["workflow_template"]) == (None, "Release")
    table = cli.invoke(app, ["test", "list", str(suite)])
    assert "Release" in table.stdout


@pytest.mark.parametrize(
    ("unsaved_reads", "result"),
    [(2, "fail"), (50, "error")],
)
def test_node_job_summaries_are_read_once_awx_has_saved_them(
    cli: CliInvoker, aap: FakeAap, tmp_path: Path, unsaved_reads: int, result: str
) -> None:
    """A node job's summaries hide its changes until AWX has saved its events."""
    _seed_release(aap)
    aap.node_outcomes["deploy"] = {
        "host_summaries": [{"host_name": "web1", "changed": 2}],
        "unsaved_reads": unsaved_reads,
    }
    suite = _suite(
        tmp_path,
        {
            "workflow": {"expect": {"changed": 0}},
            "node": {"expect": {"nodes": {"deploy": {"changed": 0}}}},
        },
    )

    code, rows, stderr = _run(cli, suite)

    assert [row["result"] for row in rows] == [result, result], stderr
    if result == "fail":
        assert code == 1
        assert [row["failure"]["system"] for row in rows] == ["awx.expectation"] * 2
        return
    assert code == 5
    workflow, node = (row["failure"] for row in rows)
    assert (workflow["system"], workflow["category"]) == ("awx.controller", "unavailable")
    assert workflow["message"].startswith("node deploy: AWX is still saving the events of job ")
    assert workflow["evidence"]["node"] == "deploy"
    assert (node["system"], node["category"]) == ("awx.controller", "unavailable")
    assert node["message"].startswith("node deploy: AWX is still saving the events of job ")
