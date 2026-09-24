"""Tests for the AwxTestSuite domain models: validation rules and the exit code."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from untaped.capabilities.awx.domain.suite import (
    Case,
    CaseResult,
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
        lambda: Case.model_validate({}),
        lambda: Suite(name="deploy", job_template="Deploy app", cases={}),
        lambda: CaseResult(suite="deploy", case="us-east", result="awesome"),
        lambda: RefSentinel(kind="", name="foo"),
        lambda: RefSentinel(kind="Inventory", name=""),
    ],
    ids=[
        "choice-without-choices",
        "default-outside-choices",
        "case-without-launch",
        "suite-without-cases",
        "unknown-result",
        "ref-without-kind",
        "ref-without-name",
    ],
)
def test_invalid_suite_shapes_are_rejected(build: Callable[[], object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        build()


def test_case_assert_alias_for_python_keyword() -> None:
    """``assert:`` is a Python keyword, the field is exposed as ``assert_``."""
    case = Case.model_validate({"launch": {"limit": "x"}, "assert": {}})
    assert case.assert_ == {}
    assert "assert" in case.model_dump(by_alias=True)


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
