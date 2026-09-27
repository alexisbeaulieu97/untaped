"""Tests for the AwxTestSuite domain models: validation rules and the exit code."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from untaped.capabilities.awx.domain.suite import (
    Case,
    CaseResult,
    Expectation,
    RefSentinel,
    Suite,
    SuiteRunOutcome,
    VariableSpec,
)


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


def test_evaluate_defaults_to_expecting_success() -> None:
    [check] = Expectation().evaluate(status="failed", log=None)
    assert check.model_dump() == {
        "check": "status",
        "expected": "successful",
        "actual": "failed",
        "passed": False,
    }


def test_evaluate_log_checks_report_the_line_that_decided_them() -> None:
    expect = Expectation.model_validate(
        {
            "status": "failed",
            "log": {
                "contains": ["msg: boom", "absent"],
                "not_contains": ["fatal:"],
                "matches": [r"changed=\d+"],
            },
        }
    )
    log = ["TASK [x]", "fatal: [web1]: FAILED! => msg: boom", "web1 : ok=1 changed=2"]
    checks = [c.model_dump() for c in expect.evaluate(status="failed", log=log)]
    assert checks == [
        {"check": "status", "expected": "failed", "actual": "failed", "passed": True},
        {"check": "log.contains", "expected": "msg: boom", "actual": log[1], "passed": True},
        {"check": "log.contains", "expected": "absent", "actual": None, "passed": False},
        {"check": "log.not_contains", "expected": "fatal:", "actual": log[1], "passed": False},
        {"check": "log.matches", "expected": r"changed=\d+", "actual": log[2], "passed": True},
    ]


def test_needs_log_only_with_log_checks() -> None:
    assert Expectation().needs_log is False
    assert Expectation.model_validate({"log": {"contains": ["x"]}}).needs_log is True


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
    outcome = SuiteRunOutcome(
        results=tuple(CaseResult(suite="s", case=str(i), result=r) for i, r in enumerate(results))
    )
    assert outcome.exit_code() == code
