"""Which system is responsible when an ``awx test`` case does not pass: the attribution rules."""

from __future__ import annotations

from typing import Any

import pytest

from untaped.capabilities.awx.domain import Job
from untaped.capabilities.awx.domain.case_failure import (
    CaseFailure,
    FailedTask,
    FailureEvidence,
    RelatedExecution,
    finished_failure,
    launch_system,
    request_failure,
    responsible_update,
    timeout_failure,
)
from untaped.capabilities.awx.errors import LaunchPromptError, ResourceNotFoundError
from untaped.capability_api import ConfigError, ErrorInfo, HttpTransportError

_PROJECT_FAILED = (
    'Previous Task Failed: {"job_type": "project_update", "job_name": "acme", "job_id": "812"}'
)
_INVENTORY_FAILED = (
    'Previous Task Failed: {"job_type": "inventory_update", "job_name": "Cloud", "job_id": "813"}'
)


def _job(status: str, **fields: Any) -> Job:
    return Job(id=5, kind="job", status=status, **fields)


def _task(status: str = "failed", *, host: str = "web1", msg: str | None = "boom") -> FailedTask:
    return FailedTask(host=host, task="Deploy", status=status, msg=msg, stderr=None)  # type: ignore[arg-type]


def _attribution(failure: CaseFailure) -> tuple[str, str, bool]:
    return failure.system, failure.category, failure.retryable


def test_a_case_failure_is_an_error_info_written_with_a_summary() -> None:
    failure = finished_failure(
        _job("successful"), reasons=["expected status failed, got successful"],
        status_held=False, failed_tasks=None,
    )  # fmt: skip
    assert isinstance(failure, ErrorInfo)
    assert failure.model_dump(mode="json") == {
        "category": "failed",
        "system": "awx.expectation",
        "retryable": False,
        "summary": "expected status failed, got successful",
        "hint": "compare expectations with what the job did; fix the change or the case",
        "evidence": {
            "job_explanation": None,
            "result_traceback": None,
            "related": None,
            "log_tail": None,
            "failed_tasks": None,
            "unreachable_hosts": None,
        },
    }
    assert CaseFailure.model_validate(failure.model_dump()) == failure


@pytest.mark.parametrize(
    ("job", "held", "tasks", "expected", "summary"),
    [
        # the job ended as asked, but a log check did not hold
        (_job("failed"), True, [_task()], ("awx.expectation", "failed", False), "no log line"),
        (_job("successful"), False, None, ("awx.expectation", "failed", False), "no log line"),
        (
            _job("error", job_explanation=_PROJECT_FAILED),
            False,
            [_task(msg="couldn't find remote ref feature/x\nmore")],
            ("awx.scm", "failed", False),
            "project update 812 for 'acme' failed: couldn't find remote ref feature/x",
        ),
        (
            _job("failed", job_explanation=_INVENTORY_FAILED),
            False,
            [],
            ("awx.inventory", "config", False),
            "inventory update 813 for 'Cloud' failed",
        ),
        (
            _job("error", result_traceback="Traceback\n  credential/__init__.py\nHTTPError: 403"),
            False,
            None,
            ("awx.credentials", "auth", False),
            "job could not use a credential: HTTPError: 403",
        ),
        (
            _job("error", job_explanation="Failed to pull image quay.io/ee"),
            False,
            None,
            ("awx.controller", "unavailable", True),
            "job ended in error: Failed to pull image quay.io/ee",
        ),
        (_job("error"), False, None, ("awx.controller", "unavailable", True), "job ended in error"),
        (
            _job("canceled"),
            False,
            None,
            ("awx.controller", "unavailable", True),
            "job was canceled outside this run",
        ),
        (
            _job("failed"),
            False,
            [_task("unreachable", host="web3", msg="ssh: timed out"), _task("unreachable")],
            ("awx.hosts", "unavailable", True),
            "unreachable: web1, web3: ssh: timed out",
        ),
        (
            _job("failed"),
            False,
            [_task("unreachable", host="db1"), _task(host="web2", msg=None), _task(host="web3")],
            ("awx.playbook", "failed", False),
            "task 'Deploy' failed on web2: boom (and 1 more)",
        ),
        (
            _job("failed"),
            False,
            [],
            ("awx.playbook", "failed", False),
            "job failed without a failed task (a syntax error or a missing role?): see log_tail",
        ),
        (
            _job("failed", job_explanation="Job terminated due to timeout"),
            False,
            None,
            ("awx.playbook", "failed", False),
            "job failed: Job terminated due to timeout",
        ),
    ],
)
def test_a_finished_job_is_attributed_by_the_first_matching_rule(
    job: Job,
    held: bool,
    tasks: list[FailedTask] | None,
    expected: tuple[str, str, bool],
    summary: str,
) -> None:
    failure = finished_failure(
        job, reasons=["no log line contains 'ok'"], status_held=held, failed_tasks=tasks
    )
    assert _attribution(failure) == expected
    assert failure.message.startswith(summary)


def test_a_failed_update_hint_names_its_log() -> None:
    failure = finished_failure(
        _job("failed", job_explanation=_INVENTORY_FAILED),
        reasons=[],
        status_held=False,
        failed_tasks=None,
    )
    assert failure.hint == "read its log: `untaped awx jobs logs 813 --kind inventory_update`"


def test_the_responsible_update_is_the_one_awx_names() -> None:
    update = responsible_update(_job("error", job_explanation=_PROJECT_FAILED))
    assert update == Job(id=812, kind="project_update", name="acme", status="failed")
    assert responsible_update(_job("failed")) is None
    other = 'Previous Task Failed: {"job_type": "workflow_job", "job_name": "x", "job_id": "9"}'
    assert responsible_update(_job("failed", job_explanation=other)) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("pending", ("awx.controller", "unavailable", True)),
        ("waiting", ("awx.controller", "unavailable", True)),
        ("running", ("awx.playbook", "failed", False)),
    ],
)
def test_a_timeout_blames_the_controller_until_the_job_runs(
    status: str, expected: tuple[str, str, bool]
) -> None:
    failure = timeout_failure(_job(status), f"still {status} after 60s; cancel requested")
    assert _attribution(failure) == expected
    assert failure.message == f"still {status} after 60s; cancel requested"


@pytest.mark.parametrize(
    ("error", "launching", "expected"),
    [
        (ConfigError("rejected", category="auth", system="awx"), True, "awx.credentials"),
        (ConfigError("denied", category="permission", system="awx"), False, "awx.credentials"),
        (LaunchPromptError("does not prompt for limit"), True, "awx.suite"),
        (ResourceNotFoundError("job_template", {"name": "x"}), True, "awx.suite"),
        (HttpTransportError("down", system="awx"), True, "awx.controller"),
        (HttpTransportError("down", system="awx"), False, "awx.controller"),
        (RuntimeError("boom"), False, "awx.controller"),
        (LaunchPromptError("no", details={"field": "scm_branch"}), True, "awx.scm"),
    ],
)
def test_a_request_failure_keeps_its_category_and_names_the_system(
    error: Exception, launching: bool, expected: str
) -> None:
    failure = request_failure(error, launching=launching)
    info = ErrorInfo.from_exception(error)
    assert (failure.system, failure.category, failure.message) == (
        expected,
        info.category,
        info.message,
    )
    if launching:
        assert launch_system(error) == expected


def test_a_request_failure_keeps_the_errors_own_hint() -> None:
    error = ConfigError("rejected", category="auth", system="awx", hint="run `untaped x`")
    failure = request_failure(error, summary="rejected; cancel requested")
    assert (failure.message, failure.hint) == ("rejected; cancel requested", "run `untaped x`")


def test_evidence_names_unreachable_hosts_and_keeps_the_end_of_a_traceback() -> None:
    job = _job("failed", job_explanation="why", result_traceback="x" * 3000 + "\nKeyError: y")
    related = RelatedExecution(kind="project_update", id=8, name="p", status="failed", url=None)
    evidence = FailureEvidence.of(
        job,
        related=related,
        log_tail=["a"],
        failed_tasks=[_task("unreachable", host="db1"), _task(), _task("unreachable", host="db1")],
    )
    assert evidence.unreachable_hosts == ("db1",)
    assert evidence.result_traceback is not None
    assert evidence.result_traceback.endswith("KeyError: y")
    assert len(evidence.result_traceback) == 2001
    assert (evidence.job_explanation, evidence.log_tail) == ("why", ("a",))
    assert evidence.related == related
    assert FailureEvidence.of(_job("failed")) == FailureEvidence()
