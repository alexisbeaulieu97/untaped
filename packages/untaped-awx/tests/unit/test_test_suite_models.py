"""Tests for the AwxTestSuite domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from untaped_awx.domain.test_suite import (
    Case,
    CaseResult,
    TestRunOutcome,
    TestSuite,
    VariableSpec,
)


def test_variable_spec_minimal() -> None:
    var = VariableSpec(name="env", type="string")
    assert var.name == "env"
    assert var.required is True  # no default → required


def test_variable_spec_with_default_is_optional() -> None:
    var = VariableSpec(name="env", type="string", default="dev")
    assert var.required is False


def test_variable_spec_choice_requires_choices_list() -> None:
    with pytest.raises(ValidationError):
        VariableSpec(name="env", type="choice", choices=())


def test_case_assert_alias_for_python_keyword() -> None:
    """``assert:`` is a Python keyword, the field is exposed as ``assert_``."""
    case = Case.model_validate({"launch": {"limit": "x"}, "assert": {}})
    assert case.assert_ == {}
    # Empty dict serialises back as ``assert``
    dumped = case.model_dump(by_alias=True)
    assert "assert" in dumped


def test_case_requires_launch_block() -> None:
    with pytest.raises(ValidationError):
        Case.model_validate({})


def test_case_assert_default_is_none() -> None:
    case = Case.model_validate({"launch": {"limit": "x"}})
    assert case.assert_ is None


def test_test_suite_minimal() -> None:
    suite = TestSuite(
        name="deploy",
        job_template="Deploy app",
        cases={"only": Case.model_validate({"launch": {}})},
    )
    assert suite.kind == "AwxTestSuite"
    assert "only" in suite.cases


def test_test_suite_rejects_no_cases() -> None:
    with pytest.raises(ValidationError):
        TestSuite(name="deploy", job_template="Deploy app", cases={})


def test_case_result_literals() -> None:
    res = CaseResult(suite="deploy", case="us-east", result="pass", job_status="successful")
    assert res.result == "pass"
    with pytest.raises(ValidationError):
        CaseResult(suite="deploy", case="us-east", result="awesome")  # type: ignore[arg-type]


def test_case_result_job_status_optional() -> None:
    res = CaseResult(suite="deploy", case="bad", result="error")
    assert res.job_status is None
    assert res.job_id is None


def test_outcome_exit_code_zero_when_all_pass() -> None:
    outcome = TestRunOutcome(
        results=(
            CaseResult(suite="s", case="a", result="pass", job_status="successful"),
            CaseResult(suite="s", case="b", result="pass", job_status="successful"),
        )
    )
    assert outcome.exit_code() == 0


@pytest.mark.parametrize("bad_result", ["fail", "error", "timeout"])
def test_outcome_exit_code_one_when_any_not_pass(bad_result: str) -> None:
    outcome = TestRunOutcome(
        results=(
            CaseResult(suite="s", case="a", result="pass", job_status="successful"),
            CaseResult(suite="s", case="b", result=bad_result),  # type: ignore[arg-type]
        )
    )
    assert outcome.exit_code() == 1
