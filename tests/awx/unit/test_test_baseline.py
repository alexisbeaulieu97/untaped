"""Comparing an ``awx test run`` with a baseline: changes, removed cases, what fails the run."""

from __future__ import annotations

import re
from typing import Any

import pytest

from untaped.capabilities.awx.domain.case_failure import CaseFailure, failure
from untaped.capabilities.awx.domain.suite import CaseResult, SuiteRunOutcome
from untaped.capabilities.awx.domain.suite_baseline import Baseline, saved_baselines
from untaped.capability_api import ErrorCategory


def _failure(category: ErrorCategory = ErrorCategory.FAILED) -> CaseFailure:
    system = "awx.playbook" if category is ErrorCategory.FAILED else "awx.controller"
    return failure(system, category, "boom")


def _row(case: str, result: str, **fields: Any) -> CaseResult:
    if result != "pass" and "failure" not in fields:
        fields["failure"] = _failure()
    return CaseResult.model_validate({"suite": "s", "case": case, "result": result, **fields})


def _base(result: str, job_id: int = 1, system: str | None = None) -> Baseline:
    return Baseline(result=result, job_id=job_id, system=system)  # type: ignore[arg-type]


def test_each_row_says_how_its_case_changed() -> None:
    outcome = SuiteRunOutcome(
        results=[
            _row("broke", "fail"),
            _row("mended", "pass"),
            _row("still", "timeout"),
            _row("kept", "pass"),
            _row("added", "fail"),
        ]
    )
    baseline = {
        ("s", "broke"): _base("pass", 10),
        ("s", "mended"): _base("fail", 11, "awx.playbook"),
        ("s", "still"): _base("error", 12, "awx.controller"),
        ("s", "kept"): _base("pass", 13),
        ("s", "gone"): _base("pass", 14),
        ("other", "gone"): _base("fail", 15, "awx.playbook"),
    }

    compared = outcome.compared(baseline, case_filter=None)

    assert [(row.suite, row.case, row.change) for row in compared.results] == [
        ("s", "broke", "regression"),
        ("s", "mended", "fixed"),
        ("s", "still", "still_failing"),
        ("s", "kept", "pass"),
        ("s", "added", "new"),
        ("s", "gone", "removed"),
        ("other", "gone", "removed"),
    ]
    broke, *_, added, gone, _ = compared.results
    assert broke.baseline == _base("pass", 10)
    assert added.baseline is None
    assert gone.model_dump(include={"result", "job_id", "baseline", "failure"}) == {
        "result": None,
        "job_id": None,
        "baseline": {"result": "pass", "job_id": 14, "system": None},
        "failure": None,
    }


def test_a_case_filter_keeps_unselected_baseline_cases_out() -> None:
    outcome = SuiteRunOutcome(results=[_row("a", "pass")])
    baseline = {("s", "a"): _base("pass"), ("s", "b"): _base("pass"), ("t", "a"): _base("pass")}

    compared = outcome.compared(baseline, case_filter={"a"})

    assert [(row.suite, row.case, row.change) for row in compared.results] == [
        ("s", "a", "pass"),
        ("t", "a", "removed"),
    ]


@pytest.mark.parametrize(
    ("rows", "exit_code", "counted"),
    [
        # only a regression fails a compared run
        ([_row("a", "fail", change="still_failing"), _row("b", "pass", change="fixed")], 0, 0),
        ([_row("a", "fail", change="new"), _row("b", "pass", change="removed")], 0, 0),
        ([_row("a", "fail", change="regression")], 1, 1),
        # the environment and retries still count, whatever the change
        (
            [_row("a", "error", change="still_failing", failure=_failure(ErrorCategory.AUTH))],
            1,
            1,
        ),
        ([_row("a", "error", change="new", failure=_failure(ErrorCategory.UNAVAILABLE))], 1, 1),
        # nothing ran now: removed rows alone are not a pass
        ([CaseResult(suite="s", case="a", result=None, change="removed")], 1, 0),
        # without a baseline every failure counts
        ([_row("a", "fail"), _row("b", "pass")], 1, 1),
    ],
)
def test_what_fails_a_run(rows: list[CaseResult], exit_code: int, counted: int) -> None:
    outcome = SuiteRunOutcome(results=rows)
    assert (outcome.exit_code(), len(outcome.counted())) == (exit_code, counted)


def test_a_run_made_now_is_a_baseline() -> None:
    outcome = SuiteRunOutcome(results=[_row("a", "pass", job_id=5), _row("b", "fail", job_id=6)])
    assert outcome.baselines() == {
        ("s", "a"): _base("pass", 5),
        ("s", "b"): _base("fail", 6, "awx.playbook"),
    }


def test_saved_rows_become_baselines() -> None:
    rows = [
        _row("a", "pass", job_id=5).model_dump(mode="json"),
        {**_row("b", "fail", job_id=6).model_dump(mode="json"), "future_field": 1},
        CaseResult(suite="s", case="c", result=None, change="removed").model_dump(mode="json"),
    ]
    assert saved_baselines(rows) == {
        ("s", "a"): _base("pass", 5),
        ("s", "b"): _base("fail", 6, "awx.playbook"),
    }


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ({"suite": "s"}, "expected a list of awx.test_result rows"),
        (
            [{"suite": "s", "case": "a"}],
            "row 1 is not an awx.test_result row: result: Field required",
        ),
        (
            [{"name": "Deploy", "id": 3}],
            "row 1 is not an awx.test_result row: suite: Field required",
        ),
        (["deploy"], "row 1 is not an awx.test_result row: row: Input should be"),
        (
            [{"suite": "s", "case": "a", "result": "pass"}] * 2,
            "row 2 repeats case s/a",
        ),
    ],
)
def test_rows_that_are_not_results_are_refused(rows: Any, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        saved_baselines(rows)
