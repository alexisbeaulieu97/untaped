"""A test case's regression expectations: ``changed``, ``hosts``, ``failed_tasks``, reruns."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from untaped_awx.domain.case_failure import FailedTask
from untaped_awx.domain.job import HostSummary
from untaped_awx.domain.suite import CaseResult, Expectation, Suite, idempotence


def _expect(body: dict[str, Any]) -> Expectation:
    return Expectation.model_validate(body)


def _suite(case: dict[str, Any], defaults: dict[str, Any] | None = None) -> Suite:
    body: dict[str, Any] = {"name": "s", "jobTemplate": "JT", "cases": {"c": case}}
    if defaults is not None:
        body["defaults"] = defaults
    return Suite.model_validate(body)


@pytest.mark.parametrize(
    "build",
    [
        lambda: _expect({"changed": -1}),
        lambda: _expect({"hosts": {"*": {"failed": -1}}}),
        lambda: _expect({"hosts": {"*": {"ok": 0}}}),
        lambda: _expect({"hosts": {"web1": {"changed": ">0"}}}),
        lambda: _expect({"failed_tasks": [{}]}),
        lambda: _expect({"failed_tasks": [{"task": "x", "stdout": "y"}]}),
        lambda: _expect({"failed_tasks": [{"matches": "("}]}),
    ],
    ids=[
        "negative-changed",
        "negative-host-bound",
        "unknown-host-counter",
        "non-numeric-bound",
        "empty-task-match",
        "unknown-task-key",
        "invalid-regex",
    ],
)
def test_invalid_regression_expectations_are_rejected(build: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        build()


@pytest.mark.parametrize(
    ("case", "defaults"),
    [
        ({"expect": {"status": "failed", "idempotent": True}}, None),
        ({"expect": {"idempotent": True}}, {"expect": {"status": "failed"}}),
        ({"expect": {"status": "error"}}, {"expect": {"idempotent": True}}),
    ],
)
def test_an_idempotent_case_must_expect_success(
    case: dict[str, Any], defaults: dict[str, Any] | None
) -> None:
    with pytest.raises(ValidationError, match="case 'c': idempotent needs status successful"):
        _suite(case, defaults)


def test_a_case_inherits_every_regression_expectation_it_leaves_unset() -> None:
    suite = _suite(
        {},
        defaults={
            "expect": {
                "changed": 0,
                "hosts": {"*": {"failed": 0}, "web1": {"changed": 0}},
                "idempotent": True,
                "failed_tasks": [{"task": "Validate"}],
            }
        },
    )

    inherited = suite.expectation("c")
    assert (inherited.changed, inherited.idempotent) == (0, True)
    assert set(inherited.hosts) == {"*", "web1"}
    assert [match.task for match in inherited.failed_tasks] == ["Validate"]

    own = _expect({"changed": 2, "idempotent": False, "failed_tasks": [{"msg": "boom"}]}).over(
        suite.expectation("c")
    )
    assert (own.changed, own.idempotent) == (2, False)
    assert [match.msg for match in own.failed_tasks] == ["boom"]


def test_host_bounds_merge_per_host_and_counter() -> None:
    defaults = _expect({"hosts": {"*": {"failed": 0, "changed": 0}, "web1": {"unreachable": 0}}})

    merged = _expect({"hosts": {"*": {"changed": 5}, "web2": {"failed": 1}}}).over(defaults)

    assert {host: bounds.limits() for host, bounds in merged.hosts.items()} == {
        "*": [("failed", 0), ("changed", 5)],
        "web1": [("unreachable", 0)],
        "web2": [("failed", 1)],
    }


def test_a_named_host_bound_overrides_star_for_that_counter_only() -> None:
    expect = _expect({"hosts": {"*": {"failed": 0, "changed": 0}, "web1": {"changed": 3}}})
    hosts = {
        "web1": HostSummary(changed=2, failed=1),
        "web2": HostSummary(changed=1),
    }

    results = expect.check_hosts(hosts)

    assert [(r.expected, r.actual, r.passed) for r in results] == [
        ("*: failed <= 0", "web1=1", False),
        ("*: changed <= 0", "web2=1", False),
        ("web1: changed <= 3", "2", True),
    ]


def test_only_host_expectations_need_the_host_summaries() -> None:
    for body in (
        {"idempotent": True, "log": {"contains": ["x"]}},
        {"status": "failed", "failed_tasks": [{"task": "x"}]},
    ):
        assert not _expect(body).needs_hosts
    for body in ({"changed": 0}, {"hosts": {"*": {"failed": 0}}}):
        assert _expect(body).needs_hosts


def test_a_negative_case_without_failed_tasks_passes_on_any_failure() -> None:
    assert _expect({"status": "failed"}).passes_on_any_failure
    assert not _expect({"status": "failed", "failed_tasks": [{"msg": "x"}]}).passes_on_any_failure
    assert not _expect({}).passes_on_any_failure


_HOSTS = {
    "web1": HostSummary(ok=3, changed=2),
    "web2": HostSummary(ok=1, changed=1, failed=1),
    "db1": HostSummary(unreachable=1),
}


def test_changed_bounds_the_total_over_every_host() -> None:
    [held] = _expect({"changed": 3}).check_hosts(_HOSTS)
    [broken] = _expect({"changed": 0}).check_hosts(_HOSTS)

    assert held.model_dump() == {
        "check": "changed",
        "expected": "<= 3",
        "actual": "3",
        "passed": True,
    }
    assert not broken.passed
    assert broken.describe_failure() == "expected <= 0 changed tasks, got 3"


def test_host_bounds_name_the_bound_that_failed() -> None:
    expect = _expect(
        {
            "hosts": {
                "*": {"failed": 0, "unreachable": 1},
                "web1": {"changed": 0},
                "web9": {"failed": 0},
            }
        }
    )

    results = expect.check_hosts(_HOSTS)

    assert [(r.expected, r.actual, r.passed) for r in results] == [
        ("*: failed <= 0", "web2=1", False),
        ("*: unreachable <= 1", None, True),
        ("web1: changed <= 0", "2", False),
        ("web9: failed <= 0", None, False),
    ]
    assert [r.describe_failure() for r in results if not r.passed] == [
        "expected *: failed <= 0, got web2=1",
        "expected web1: changed <= 0, got 2",
        "expected web9: failed <= 0, but the host is not in the job's host summaries",
    ]


def test_a_cut_host_list_names_the_filters_that_find_every_host_a_check_needs() -> None:
    expect = _expect(
        {
            "changed": 0,
            "hosts": {"*": {"failed": 0, "changed": 0}, "web1": {"changed": 0}, "web9": {}},
        }
    )
    assert expect.host_filters(known={"web1"}) == [
        {"changed__gt": "0"},
        {"failures__gt": "0"},
        {"host_name__in": "web9"},
    ]
    assert _expect({"log": {"contains": ["x"]}}).host_filters(known=set()) == []


def _task(task: str, msg: str | None, host: str = "web1") -> FailedTask:
    return FailedTask(host=host, task=task, status="failed", msg=msg, stderr=None)


def test_failed_task_expectations_match_task_and_message() -> None:
    tasks = (
        _task("Install packages", "No package matching 'x'", host="db1"),
        _task("Validate input", "environment must be one of staging, prod"),
    )
    expect = _expect(
        {
            "failed_tasks": [
                {"task": "Validate", "msg": "must be one of"},
                {"matches": r"environment must be one of \w+"},
                {"task": "Validate", "msg": "app_version"},
            ]
        }
    )

    results = expect.check_failed_tasks(tasks)

    assert [(r.expected, r.passed) for r in results] == [
        ("task 'Validate', msg 'must be one of'", True),
        (r"msg matches 'environment must be one of \w+'", True),
        ("task 'Validate', msg 'app_version'", False),
    ]
    assert results[0].actual == "[web1] Validate input: environment must be one of staging, prod"
    assert results[2].describe_failure() == (
        "no failed task matches task 'Validate', msg 'app_version'"
    )


def test_a_task_without_a_message_matches_only_a_name_check() -> None:
    [by_name, by_msg] = _expect(
        {"failed_tasks": [{"task": "Boom"}, {"msg": "x"}]}
    ).check_failed_tasks((_task("Boom", None),))
    assert (by_name.passed, by_msg.passed) == (True, False)


def _rerun(result: str, **fields: Any) -> CaseResult:
    return CaseResult.model_validate({"suite": "s", "case": "c", "result": result, **fields})


def test_the_idempotence_check_comes_from_the_rerun() -> None:
    changed = [{"check": "changed", "expected": "<= 0", "actual": "3", "passed": False}]

    held = idempotence(
        _rerun(
            "pass",
            job_id=6,
            job_status="successful",
            expectations=[{**changed[0], "actual": "0", "passed": True}],
        )
    )
    broken = idempotence(_rerun("fail", job_id=6, job_status="successful", expectations=changed))

    assert held.model_dump() == {
        "check": "idempotent",
        "expected": "successful, 0 changed",
        "actual": "successful, 0 changed",
        "passed": True,
    }
    assert (broken.actual, broken.passed) == ("successful, 3 changed", False)
    assert broken.describe_failure() == "not idempotent: the rerun ended successful, 3 changed"
    assert idempotence(_rerun("error", job_id=6)).actual == "unknown"
    assert idempotence(_rerun("error")).actual == "not launched"
