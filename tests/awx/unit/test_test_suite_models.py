"""Tests for the AwxTestSuite domain models: validation rules and the exit code."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from untaped.capabilities.awx.domain import JobEvent
from untaped.capabilities.awx.domain.case_failure import FailedTask, failure
from untaped.capabilities.awx.domain.suite import (
    Case,
    CaseResult,
    Expectation,
    HostBounds,
    NodeExpectation,
    RefSentinel,
    Suite,
    SuiteRunOutcome,
    TemplateBinding,
    VariableSpec,
)
from untaped.sdk import ErrorCategory


def test_variable_spec_is_required_unless_it_has_a_default() -> None:
    assert VariableSpec(name="env", type="string").required is True
    assert VariableSpec(name="env", type="string", default="dev").required is False
    assert VariableSpec(name="env", type="choice", choices=("a", "b"), default="a").default == "a"


@pytest.mark.parametrize(
    "build",
    [
        lambda: VariableSpec(name="env", type="choice", choices=()),
        lambda: VariableSpec(name="env", type="choice", choices=("a", "b"), default="c"),
        lambda: Case.model_validate({"assert": {}}),
        lambda: Case.model_validate({"timeout": 0}),
        lambda: Expectation.model_validate({"status": "running"}),
        lambda: Expectation.model_validate({"log": {"matches": ["("]}}),
        lambda: Suite(name="deploy", job_template="Deploy app", cases={}),
        lambda: CaseResult(suite="deploy", case="us-east", result="awesome"),
        lambda: RefSentinel(kind="", name="foo"),
        lambda: RefSentinel(kind="Inventory", name=""),
    ],
    ids=[
        "choice-without-choices",
        "default-outside-choices",
        "assert-block",
        "zero-timeout",
        "non-terminal-status",
        "invalid-regex",
        "suite-without-cases",
        "unknown-result",
        "ref-without-kind",
        "ref-without-name",
    ],
)
def test_invalid_suite_shapes_are_rejected(build: Callable[[], object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        build()


def test_case_launch_defaults_to_empty() -> None:
    assert Case.model_validate({}).launch == {}


def test_case_expectation_overrides_defaults_per_key() -> None:
    """``status`` and each ``log`` list replace the default's; the rest is inherited."""
    defaults = Expectation.model_validate(
        {"status": "failed", "log": {"contains": ["PLAY RECAP"], "not_contains": ["WARN"]}}
    )
    case = Expectation.model_validate({"log": {"contains": ["done"]}})
    merged = case.over(defaults)
    assert merged.status == "failed"
    assert merged.log.contains == ("done",)
    assert merged.log.not_contains == ("WARN",)


def test_status_defaults_to_expecting_success() -> None:
    assert Expectation().check_status("failed").model_dump() == {
        "check": "status",
        "expected": "successful",
        "actual": "failed",
        "passed": False,
    }


def test_log_checks_report_the_line_that_decided_them() -> None:
    expect = Expectation.model_validate(
        {
            "log": {
                "contains": ["msg: boom", "absent"],
                "not_contains": ["fatal:"],
                "matches": [r"changed=\d+"],
            },
        }
    )
    log = ["TASK [x]", "fatal: [web1]: FAILED! => msg: boom", "web1 : ok=1 changed=2"]
    assert expect.needs_log
    assert [c.model_dump() for c in expect.log.evaluate(log)] == [
        {"check": "log.contains", "expected": "msg: boom", "actual": log[1], "passed": True},
        {"check": "log.contains", "expected": "absent", "actual": None, "passed": False},
        {"check": "log.not_contains", "expected": "fatal:", "actual": log[1], "passed": False},
        {"check": "log.matches", "expected": r"changed=\d+", "actual": log[2], "passed": True},
    ]
    assert not Expectation().needs_log


def test_failure_descriptions_quote_patterns_verbatim_and_clip_long_lines() -> None:
    expect = Expectation.model_validate({"log": {"matches": [r"ok=\d+"], "not_contains": ["x"]}})
    contained, matched = expect.log.evaluate(["x" * 1000])
    assert matched.describe_failure() == r"no log line matches 'ok=\d+'"
    assert len(contained.actual or "") == 301


@pytest.mark.parametrize(
    ("results", "code"),
    [
        (("pass", "pass"), 0),
        (("pass", "fail"), 1),
        (("pass", "error"), 1),
        (("pass", "timeout"), 1),
        # nothing ran means nothing was tested: a failure for a test runner
        ((), 1),
    ],
)
def test_outcome_exit_code(results: tuple[str, ...], code: int) -> None:
    boom = failure("awx.playbook", ErrorCategory.FAILED, "boom")
    outcome = SuiteRunOutcome(
        results=tuple(
            CaseResult(suite="s", case=str(i), result=r, failure=None if r == "pass" else boom)
            for i, r in enumerate(results)
        )
    )
    assert bool(outcome.counted()) == bool(code)


def _failure_event(event: str, res: dict[str, object] | None, **fields: object) -> JobEvent:
    return JobEvent.model_validate(
        {"counter": 1, "event": event, "failed": True, "event_data": {"res": res}, **fields}
    )


def test_failed_task_names_host_task_and_messages() -> None:
    event = _failure_event(
        "runner_on_failed",
        {"msg": "non-zero return code", "stderr": "missing file"},
        host_name="web1",
        task="Run migrations",
    )
    assert FailedTask.from_event(event).model_dump() == {
        "host": "web1",
        "task": "Run migrations",
        "status": "failed",
        "msg": "non-zero return code",
        "stderr": "missing file",
    }


def test_unreachable_hosts_and_bare_events_still_make_failed_tasks() -> None:
    unreachable = _failure_event("runner_on_unreachable", {"msg": ["ssh", "timeout"]})
    assert FailedTask.from_event(unreachable).model_dump() == {
        "host": None,
        "task": None,
        "status": "unreachable",
        "msg": "['ssh', 'timeout']",
        "stderr": None,
    }
    assert FailedTask.from_event(_failure_event("runner_on_failed", None)).msg is None


def test_failed_task_clips_long_messages_keeping_the_end_of_stderr() -> None:
    task = FailedTask.from_event(
        _failure_event("runner_on_failed", {"msg": "m" * 5000, "stderr": "s" * 5000 + "END"})
    )
    assert task.msg is not None and task.msg.endswith("…") and len(task.msg) == 1001
    assert task.stderr is not None and task.stderr.startswith("…")
    assert task.stderr.endswith("END") and len(task.stderr) == 1001


def test_a_suite_organization_overrides_the_default_scope() -> None:
    suite = Suite.model_validate({"name": "s", "jobTemplate": "jt", "cases": {"c": {}}})
    assert suite.scope({"organization": "Default"}) == {"organization": "Default"}
    ops = suite.model_copy(update={"organization": "Ops"})
    assert ops.scope(None) == {"organization": "Ops"}
    assert ops.scope({"organization": "Default"}) == {"organization": "Ops"}


# ---- workflow suites -----------------------------------------------------


def _workflow_suite(**body: object) -> Suite:
    return Suite.model_validate(
        {"name": "release", "workflowTemplate": "Release", "cases": {"c": {}}, **body}
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"cases": {"c": {}}}, "a suite needs jobTemplate or workflowTemplate"),
        (
            {"jobTemplate": "Deploy", "workflowTemplate": "Release", "cases": {"c": {}}},
            "a suite names jobTemplate or workflowTemplate, and this one names both",
        ),
        (
            {"jobTemplate": "Deploy", "cases": {"c": {"approvals": "approve"}}},
            "case 'c': approvals applies to a workflowTemplate suite only",
        ),
        (
            {"jobTemplate": "Deploy", "cases": {"c": {"expect": {"nodes": {"a": {}}}}}},
            "case 'c': expect.nodes applies to a workflowTemplate suite only",
        ),
        (
            {
                "workflowTemplate": "Release",
                "cases": {"c": {"expect": {"log": {"contains": ["x"]}}}},
            },
            "case 'c': a workflow job has no log; check the log of a node under expect.nodes",
        ),
    ],
    ids=["no-template", "both-templates", "job-approvals", "job-nodes", "workflow-log"],
)
def test_a_suite_launches_one_job_or_workflow_template(
    body: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Suite.model_validate({"name": "s", **body})


def test_a_suite_binds_to_its_template_kind_name_and_scope() -> None:
    job = Suite.model_validate({"name": "s", "jobTemplate": "Deploy", "cases": {"c": {}}})
    workflow = _workflow_suite(organization="Ops")

    assert job.binding(None) == TemplateBinding("JobTemplate", "Deploy", None)
    assert workflow.binding({"organization": "Default"}) == TemplateBinding(
        "WorkflowJobTemplate", "Release", {"organization": "Ops"}
    )


def test_a_case_inherits_approvals_from_the_defaults() -> None:
    suite = _workflow_suite(
        defaults={"approvals": "approve"},
        cases={"inherits": {}, "denies": {"approvals": "deny"}},
    )

    assert suite.approvals("inherits") == "approve"
    assert suite.approvals("denies") == "deny"
    assert _workflow_suite().approvals("c") is None


def test_node_expectations_merge_node_by_node_over_the_defaults() -> None:
    suite = _workflow_suite(
        defaults={
            "expect": {
                "nodes": {
                    "deploy": {"hosts": {"*": {"failed": 0}}, "log": {"contains": ["PLAY"]}},
                    "verify": {"status": "successful"},
                }
            }
        },
        cases={
            "c": {
                "expect": {
                    "nodes": {
                        "deploy": {"hosts": {"*": {"changed": 0}}},
                        "rollback": {"status": "never_ran"},
                    }
                }
            }
        },
    )

    nodes = suite.expectation("c").nodes

    assert set(nodes) == {"deploy", "verify", "rollback"}
    assert nodes["deploy"].hosts["*"] == HostBounds(failed=0, changed=0)
    assert nodes["deploy"].log.contains == ("PLAY",)
    assert nodes["verify"].status == "successful"
    assert nodes["rollback"].status == "never_ran"


def test_never_ran_in_a_case_replaces_the_defaults_node_entry() -> None:
    suite = _workflow_suite(
        defaults={"expect": {"nodes": {"deploy": {"hosts": {"*": {"failed": 0}}}}}},
        cases={"c": {"expect": {"nodes": {"deploy": {"status": "never_ran"}}}}},
    )

    assert suite.expectation("c").nodes["deploy"] == NodeExpectation(status="never_ran")


def test_an_invalid_merged_node_entry_names_its_case_and_node() -> None:
    with pytest.raises(ValidationError, match="case 'c': node 'deploy': a node expected never"):
        _workflow_suite(
            defaults={"expect": {"nodes": {"deploy": {"status": "never_ran"}}}},
            cases={"c": {"expect": {"nodes": {"deploy": {"changed": 0}}}}},
        )


def test_node_checks_that_pin_the_cause_silence_the_negative_case_warning() -> None:
    suite = _workflow_suite(
        cases={
            "bare": {"expect": {"status": "failed"}},
            "pinned": {"expect": {"status": "failed", "nodes": {"approve": {"status": "failed"}}}},
            "tasks": {
                "expect": {
                    "status": "failed",
                    "nodes": {"deploy": {"failed_tasks": [{"task": "x"}]}},
                }
            },
        },
    )

    assert [suite.expectation(name).passes_on_any_failure for name in suite.cases] == [
        True,
        False,
        False,
    ]


def test_a_case_warns_about_approvals_it_does_not_answer() -> None:
    suite = _workflow_suite(cases={"silent": {}, "answered": {"approvals": "approve"}})

    assert suite.case_warnings("silent", approval_nodes=["release/approve"]) == [
        "release/silent: the workflow has approval nodes (release/approve) and the case sets "
        "no approvals, so a pending approval fails it"
    ]
    assert suite.case_warnings("answered", approval_nodes=["approve"]) == []
    assert suite.case_warnings("silent", approval_nodes=[]) == []


@pytest.mark.parametrize(
    "node",
    [
        {"status": "never_ran", "changed": 0},
        {"idempotent": True},
        {"nodes": {"inner": {}}},
        {"status": "running"},
    ],
    ids=["never-ran-with-checks", "idempotent", "nested-nodes", "non-terminal"],
)
def test_invalid_node_expectations_are_rejected(node: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NodeExpectation.model_validate(node)


def test_a_node_check_names_its_node() -> None:
    check = NodeExpectation().check_status("never_ran").model_copy(update={"node": "deploy"})

    assert check.describe_failure() == "node deploy: expected status successful, got never_ran"
    assert check.model_dump()["node"] == "deploy"
    # A job case's checks carry no node key at all.
    assert "node" not in Expectation().check_status("successful").model_dump()
