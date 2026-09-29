"""A test case's regression expectations: ``changed``, ``hosts``, ``failed_tasks``, reruns."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from untaped.capabilities.awx.domain.case_failure import FailedTask
from untaped.capabilities.awx.domain.job import HostSummary
from untaped.capabilities.awx.domain.suite import Expectation


def _expect(body: dict[str, Any]) -> Expectation:
    return Expectation.model_validate(body)


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


def test_a_case_inherits_every_regression_expectation_it_leaves_unset() -> None:
    defaults = _expect(
        {
            "changed": 0,
            "hosts": {"*": {"failed": 0}, "web1": {"changed": 0}},
            "idempotent": True,
            "failed_tasks": [{"task": "Validate"}],
        }
    )

    inherited = _expect({}).over(defaults)
    assert (inherited.changed, inherited.idempotent) == (0, True)
    assert set(inherited.hosts) == {"*", "web1"}
    assert [match.task for match in inherited.failed_tasks] == ["Validate"]

    # Set values replace the default's; ``hosts`` replaces host by host.
    own = _expect(
        {
            "changed": 2,
            "hosts": {"web1": {"changed": 3}},
            "idempotent": False,
            "failed_tasks": [{"msg": "boom"}],
        }
    ).over(defaults)
    assert (own.changed, own.idempotent) == (2, False)
    assert own.hosts["web1"].changed == 3
    assert own.hosts["*"].failed == 0
    assert [match.msg for match in own.failed_tasks] == ["boom"]


def test_only_host_and_task_expectations_need_the_host_summaries() -> None:
    assert not _expect({"idempotent": True, "log": {"contains": ["x"]}}).needs_hosts
    for body in (
        {"changed": 0},
        {"hosts": {"*": {"failed": 0}}},
        {"failed_tasks": [{"task": "x"}]},
    ):
        assert _expect(body).needs_hosts


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
        "expected web9: failed <= 0, but web9 is not in the job's host summaries",
    ]


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


def test_idempotence_check_reports_the_rerun() -> None:
    held = Expectation.check_rerun("successful", 0)
    changed = Expectation.check_rerun("successful", 3)
    failed = Expectation.check_rerun("failed", None)

    assert held.model_dump() == {
        "check": "idempotent",
        "expected": "successful, 0 changed",
        "actual": "successful, 0 changed",
        "passed": True,
    }
    assert (changed.actual, changed.passed) == ("successful, 3 changed", False)
    assert (
        changed.describe_failure()
        == "rerun expected successful, 0 changed, got successful, 3 changed"
    )
    assert (failed.actual, failed.passed) == ("failed", False)
