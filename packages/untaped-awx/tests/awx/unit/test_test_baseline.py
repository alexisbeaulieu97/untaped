"""Comparing an ``awx test run`` with a baseline: changes, removed cases, what fails the run."""

from __future__ import annotations

import re
from typing import Any

import pytest

from untaped.sdk import ErrorCategory
from untaped_awx.domain.case_failure import CaseFailure, failure, in_node
from untaped_awx.domain.suite import Baseline, CaseResult, SuiteRunOutcome
from untaped_awx.domain.suite_baseline import saved_baselines

_SYSTEMS = {
    ErrorCategory.FAILED: "awx.playbook",
    ErrorCategory.UNAVAILABLE: "awx.controller",
    ErrorCategory.AUTH: "awx.credentials",
}


def _failure(
    category: ErrorCategory = ErrorCategory.FAILED, system: str | None = None
) -> CaseFailure:
    return failure(system or _SYSTEMS[category], category, "boom")


def _row(case: str, result: str, **fields: Any) -> CaseResult:
    if result != "pass" and "failure" not in fields:
        fields["failure"] = _failure()
    return CaseResult.model_validate({"suite": "s", "case": case, "result": result, **fields})


def _in(node: str) -> CaseFailure:
    """A playbook failure in a workflow's ``node``."""
    return in_node(_failure(), node)


def _base(
    result: str,
    job_id: int = 1,
    system: str | None = None,
    category: ErrorCategory | None = None,
    node: str | None = None,
) -> Baseline:
    if result != "pass" and system is not None and category is None:
        category = ErrorCategory.FAILED
    return Baseline.model_validate(
        {"result": result, "job_id": job_id, "system": system, "category": category, "node": node}
    )


def test_each_row_says_how_its_case_changed() -> None:
    outcome = SuiteRunOutcome(
        results=[
            _row("broke", "fail"),
            _row("mended", "pass"),
            _row("still", "fail"),
            _row("kept", "pass"),
            _row("added", "fail"),
        ]
    )
    baseline = {
        ("s", "broke"): _base("pass", 10),
        ("s", "mended"): _base("fail", 11, "awx.playbook"),
        ("s", "still"): _base("fail", 12, "awx.playbook"),
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
    assert gone.model_dump(
        include={"result", "job_id", "baseline", "failure", "expectations", "hosts_truncated"}
    ) == {
        "result": None,
        "job_id": None,
        "baseline": {
            "result": "pass",
            "job_id": 14,
            "system": None,
            "category": None,
            "node": None,
        },
        "failure": None,
        "expectations": (),
        "hosts_truncated": False,
    }


@pytest.mark.parametrize(
    ("baseline", "now", "change"),
    [
        # the same failure as before is only reported
        (_base("fail", system="awx.playbook"), _failure(), "still_failing"),
        # another system failing now is a regression
        (_base("fail", system="awx.scm"), _failure(), "regression"),
        # a workflow still failing in the same node, or now in another one
        (_base("fail", system="awx.playbook", node="deploy"), _in("deploy"), "still_failing"),
        (_base("fail", system="awx.playbook", node="deploy"), _in("verify"), "regression"),
        (_base("fail", system="awx.playbook"), _in("verify"), "regression"),
        (
            _base("error", system="awx.suite", category=ErrorCategory.INVALID),
            _failure(),
            "regression",
        ),
        # a baseline that failed for the environment proves nothing
        (
            _base("error", system="awx.controller", category=ErrorCategory.UNAVAILABLE),
            _failure(),
            "unverified",
        ),
        (
            _base("error", system="awx.credentials", category=ErrorCategory.AUTH),
            _failure(ErrorCategory.AUTH),
            "unverified",
        ),
        # an older file without the failure matches on the result only
        (_base("fail"), _failure(), "still_failing"),
    ],
)
def test_a_case_still_fails_only_for_the_same_reason(
    baseline: Baseline, now: CaseFailure, change: str
) -> None:
    outcome = SuiteRunOutcome(results=[_row("a", "fail", failure=now)])
    [row] = outcome.compared({("s", "a"): baseline}, case_filter=None).results
    assert row.change == change


def test_a_case_filter_keeps_unselected_baseline_cases_out() -> None:
    outcome = SuiteRunOutcome(results=[_row("a", "pass")])
    baseline = {("s", "a"): _base("pass"), ("s", "b"): _base("pass"), ("t", "a"): _base("pass")}

    compared = outcome.compared(baseline, case_filter={"a"})

    assert [(row.suite, row.case, row.change) for row in compared.results] == [
        ("s", "a", "pass"),
        ("t", "a", "removed"),
    ]


_UNAVAILABLE = _failure(ErrorCategory.UNAVAILABLE)


@pytest.mark.parametrize(
    ("rows", "counted"),
    [
        # a failure the baseline already had is only reported
        ([_row("a", "fail", change="still_failing"), _row("b", "pass", change="fixed")], 0),
        ([_row("a", "pass", change="new"), _row("b", "pass", change="removed")], 0),
        ([_row("a", "fail", change="regression")], 1),
        ([_row("a", "fail", change="unverified")], 1),
        # a new case has nothing to regress against: it counts as without a baseline
        ([_row("a", "fail", change="new")], 1),
        # the environment and retries still count when the failure was there before
        ([_row("a", "error", change="still_failing", failure=_UNAVAILABLE)], 1),
        # without a baseline every failure counts
        ([_row("a", "fail"), _row("b", "pass")], 1),
    ],
)
def test_what_fails_a_run(rows: list[CaseResult], counted: int) -> None:
    assert len(SuiteRunOutcome(results=rows).counted()) == counted


def test_a_run_where_no_case_ran_fails() -> None:
    removed = CaseResult(suite="s", case="a", result=None, change="removed")
    for rows in ([], [removed]):
        [nothing] = SuiteRunOutcome(results=rows).counted()
        assert (nothing.system, nothing.category, nothing.message) == (
            "awx.suite",
            "not_found",
            "no case ran",
        )


def test_the_failures_a_baseline_run_carries_count_too() -> None:
    outcome = SuiteRunOutcome(results=[_row("a", "pass")], carried=(_UNAVAILABLE,))
    assert outcome.counted() == [_UNAVAILABLE]


def test_saved_rows_become_baselines() -> None:
    rows = [
        _row("a", "pass", job_id=5).model_dump(mode="json"),
        {**_row("b", "fail", job_id=6).model_dump(mode="json"), "future_field": 1},
        CaseResult(suite="s", case="c", result=None, change="removed").model_dump(mode="json"),
        # an older row: its failure is not known
        {"suite": "s", "case": "d", "result": "fail", "job_id": 7, "failure_reason": "boom"},
        _row("e", "fail", job_id=8, failure=_in("deploy")).model_dump(mode="json"),
    ]
    assert saved_baselines(rows) == {
        ("s", "a"): _base("pass", 5),
        ("s", "b"): _base("fail", 6, "awx.playbook", ErrorCategory.FAILED),
        ("s", "d"): _base("fail", 7),
        ("s", "e"): _base("fail", 8, "awx.playbook", ErrorCategory.FAILED, "deploy"),
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
        (["deploy"], "row 1 is not an awx.test_result row: Input should be"),
        (
            [{"suite": "s", "case": "a", "result": "pass"}] * 2,
            "row 2 repeats case s/a",
        ),
    ],
)
def test_rows_that_are_not_results_are_refused(rows: Any, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        saved_baselines(rows)
