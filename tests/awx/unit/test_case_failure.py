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
    failure_system,
    finished_failure,
    request_failure,
    responsible_update,
    timeout_failure,
    unrescued,
)
from untaped.capabilities.awx.domain.job import HostSummary
from untaped.capabilities.awx.errors import LaunchPromptError, ResourceNotFoundError
from untaped.capability_api import ConfigError, ErrorInfo, HttpTransportError

_PROJECT_FAILED = (
    'Previous Task Failed: {"job_type": "project_update", "job_name": "acme", "job_id": "812"}'
)
_STATUS_REASON = ["expected status successful, got failed"]


def _job(status: str, **fields: Any) -> Job:
    return Job(id=5, kind="job", status=status, **fields)


def _update(status: str, kind: str = "project_update", **fields: Any) -> Job:
    return Job(id=812, kind=kind, name="acme", status=status, **fields)


def _task(status: str = "failed", *, host: str = "web1", msg: str | None = "boom") -> FailedTask:
    return FailedTask(host=host, task="Deploy", status=status, msg=msg, stderr=None)  # type: ignore[arg-type]


def _attribution(failure: CaseFailure | None) -> tuple[str, str, bool]:
    assert failure is not None
    return failure.system, failure.category, failure.retryable


def _finished(
    job: Job,
    *,
    update: Job | None = None,
    held: bool = False,
    reasons: list[str] | None = None,
    tasks: list[FailedTask] | None = None,
) -> CaseFailure | None:
    return finished_failure(
        job,
        update=update,
        update_url="https://aap/#/jobs/project/812/output",
        status_held=held,
        reasons=_STATUS_REASON if reasons is None else reasons,
        failed_tasks=tasks,
    )


def test_a_case_failure_is_an_error_info_with_evidence() -> None:
    failure = _finished(_job("successful"), reasons=["expected status failed, got successful"])
    assert isinstance(failure, ErrorInfo)
    assert failure.model_dump(mode="json") == {
        "category": "failed",
        "system": "awx.expectation",
        "retryable": False,
        "message": "expected status failed, got successful",
        "hint": "compare expectations with what the job did; fix the change or the case",
        "evidence": {
            "job_explanation": None,
            "result_traceback": None,
            "related": None,
            "log_tail": None,
            "failed_tasks": None,
            "unreachable_hosts": None,
            "changed_tasks": None,
            "note": None,
        },
    }
    assert CaseFailure.model_validate(failure.model_dump()) == failure


def test_a_failed_update_is_blamed_even_when_the_case_expected_a_failure() -> None:
    """A negative case whose playbook never ran must not pass, nor blame the expectation."""
    job = _job("failed", job_explanation=_PROJECT_FAILED)
    failure = _finished(
        job,
        update=_update("failed"),
        held=True,
        reasons=[],
        tasks=[_task(msg="couldn't find remote ref feature/x\nmore")],
    )
    assert _attribution(failure) == ("awx.scm", "failed", False)
    assert failure is not None
    assert (
        failure.message
        == "project update 812 for 'acme' failed: couldn't find remote ref feature/x"
    )
    assert failure.evidence.related == RelatedExecution(
        kind="project_update",
        id=812,
        name="acme",
        status="failed",
        url="https://aap/#/jobs/project/812/output",
    )


@pytest.mark.parametrize(
    ("update", "expected", "message"),
    [
        (
            _update("failed", kind="inventory_update"),
            ("awx.inventory", "config", False),
            "inventory update 812 for 'acme' failed",
        ),
        (
            _update("error", job_explanation="Failed to pull image quay.io/ee"),
            ("awx.controller", "unavailable", True),
            "project update 812 for 'acme' ended in error: Failed to pull image quay.io/ee",
        ),
        (
            _update("error", result_traceback="Traceback\nCredentialLookupError: vault said no"),
            ("awx.credentials", "auth", False),
            "project update 812 for 'acme' could not use a credential: "
            "CredentialLookupError: vault said no",
        ),
    ],
)
def test_a_failed_update_is_attributed_by_its_real_status(
    update: Job, expected: tuple[str, str, bool], message: str
) -> None:
    failure = _finished(_job("error", job_explanation=_PROJECT_FAILED), update=update)
    assert _attribution(failure) == expected
    assert failure is not None
    assert failure.message == message
    assert failure.evidence.related is not None
    assert failure.evidence.related.status == update.status


def test_the_update_hint_names_its_log() -> None:
    failure = _finished(_job("failed"), update=_update("failed", kind="inventory_update"))
    assert failure is not None
    assert failure.hint == "read its log: `untaped awx jobs logs 812 --kind inventory_update`"


@pytest.mark.parametrize(
    ("job", "held", "tasks", "expected", "message"),
    [
        # the job ran as asked, but a log check did not hold
        (_job("failed"), True, [_task()], ("awx.expectation", "failed", False), "no log line"),
        (_job("successful"), False, None, ("awx.expectation", "failed", False), "no log line"),
        (
            _job(
                "error", result_traceback="Traceback\n  credential/x.py\nCredentialLookupError: 403"
            ),
            False,
            None,
            ("awx.credentials", "auth", False),
            "job could not use a credential: CredentialLookupError: 403",
        ),
        # a credential frame in the traceback is not a credential failure
        (
            _job("error", result_traceback="  awx/main/models/credential/__init__.py\nKeyError: x"),
            False,
            None,
            ("awx.controller", "unavailable", True),
            "job ended in error: KeyError: x",
        ),
        # nor is a transport failure a credential plugin raised
        (
            _job("error", result_traceback="Traceback\nCredentialError: connection refused"),
            False,
            None,
            ("awx.controller", "unavailable", True),
            "job ended in error: CredentialError: connection refused",
        ),
        # even when the case expected the error: the controller did not run the playbook
        (_job("error"), True, None, ("awx.controller", "unavailable", True), "job ended in error"),
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
            _job("failed", event_processing_finished=True),
            False,
            [],
            ("awx.playbook", "failed", False),
            "job failed without a failed task (a syntax error or a missing role?): see log_tail",
        ),
        (
            _job("failed", job_explanation="Job terminated due to timeout"),
            False,
            [],
            ("awx.playbook", "failed", False),
            "job failed: Job terminated due to timeout",
        ),
        # the reaper failed a job AWX lost track of: not the playbook
        (
            _job(
                "failed",
                job_explanation="Task was marked as running but was not present in the job "
                "queue, so it has been marked as failed.",
            ),
            False,
            [],
            ("awx.controller", "unavailable", True),
            "job failed: Task was marked as running",
        ),
        # no failed task read yet: never blame the playbook for an empty list
        (
            _job("failed", event_processing_finished=False),
            False,
            None,
            ("awx.controller", "unavailable", True),
            "job failed, but AWX has not processed its events yet",
        ),
        (
            _job("failed"),
            False,
            None,
            ("awx.controller", "unavailable", True),
            "job failed, but its events could not be read",
        ),
    ],
)
def test_a_finished_job_is_attributed_by_the_first_matching_rule(
    job: Job,
    held: bool,
    tasks: list[FailedTask] | None,
    expected: tuple[str, str, bool],
    message: str,
) -> None:
    failure = _finished(job, held=held, reasons=["no log line contains 'ok'"], tasks=tasks)
    assert _attribution(failure) == expected
    assert failure is not None
    assert failure.message.startswith(message)


def test_a_case_whose_expectations_hold_has_no_failure() -> None:
    assert _finished(_job("failed"), held=True, reasons=[], tasks=[_task()]) is None
    assert _finished(_job("successful"), held=True, reasons=[]) is None
    # a case may expect the controller's error (only a failed update overrides that)
    assert _finished(_job("error"), held=True, reasons=[]) is None


@pytest.mark.parametrize(
    ("explanation", "expected"),
    [
        (_PROJECT_FAILED, ("project_update", "acme", 812)),
        (
            'Previous Task Failed: {"job_type": "inventory_update", "job_name": "Cloud - aws", '
            '"job_id": 813}',
            ("inventory_update", "Cloud - aws", 813),
        ),
        # AWX does not escape names: a quote or backslash breaks the JSON
        (
            'Previous Task Failed: {"job_type": "project_update", "job_name": "a "b" \\c", '
            '"job_id": "9"}',
            ("project_update", 'a "b" \\c', 9),
        ),
        (
            'Previous Task Failed: {"job_type": "workflow_job", "job_name": "x", "job_id": "9"}',
            None,
        ),
        ('Previous Task Failed: {"job_type": "project_update", "job_id": "x"}', None),
        ("Previous Task Failed: {not json", None),
        ("Job terminated due to timeout", None),
        ("", None),
        (None, None),
    ],
)
def test_the_responsible_update_is_the_one_awx_names(
    explanation: str | None, expected: tuple[str, str, int] | None
) -> None:
    update = responsible_update(_job("failed", job_explanation=explanation))
    actual = None if update is None else (update.kind, update.name, update.id)
    assert actual == expected


def test_rescued_failures_are_not_failed_tasks() -> None:
    """Ansible reports a task a ``rescue`` block handled as failed; the recap does not."""
    tasks = [_task(host="web1"), _task(host="web2"), _task("unreachable", host="web3")]
    hosts = {"web1": HostSummary(rescued=1), "web2": HostSummary(failed=1)}
    assert [task.host for task in unrescued(tasks, hosts)] == ["web2", "web3"]
    assert unrescued(tasks, None) == tuple(tasks)


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
        (ResourceNotFoundError("job", {"id": 5}), False, "awx.controller"),
        (HttpTransportError("down", system="awx"), True, "awx.controller"),
        (HttpTransportError("down", system="awx"), False, "awx.controller"),
        (LaunchPromptError("no", details={"field": "scm_branch"}), True, "awx.scm"),
        # not AWX's doing: an untaped bug stays untaped, local setup stays local
        (RuntimeError("boom"), False, "untaped"),
        (ConfigError("awx.base_url is not configured"), True, "local"),
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
    assert failure_system(error, launching=launching) == expected


def test_a_request_failure_keeps_the_errors_own_hint() -> None:
    error = ConfigError("rejected", category="auth", system="awx", hint="run `untaped x`")
    failure = request_failure(error, message="rejected; cancel requested")
    assert (failure.message, failure.hint) == ("rejected; cancel requested", "run `untaped x`")


def test_evidence_names_unreachable_hosts_and_keeps_the_end_of_a_traceback() -> None:
    job = _job("failed", job_explanation="why", result_traceback="x" * 3000 + "\nKeyError: y")
    related = RelatedExecution(kind="project_update", id=8, name="p", status="failed", url=None)
    evidence = FailureEvidence.of(
        job,
        related=related,
        log_tail=["a"],
        failed_tasks=[_task("unreachable", host="db1"), _task(), _task("unreachable", host="db1")],
        note="log fetch failed: 502",
    )
    assert evidence.unreachable_hosts == ("db1",)
    assert evidence.result_traceback is not None
    assert evidence.result_traceback.endswith("KeyError: y")
    assert len(evidence.result_traceback) == 2001
    assert (evidence.job_explanation, evidence.log_tail) == ("why", ("a",))
    assert (evidence.related, evidence.note) == (related, "log fetch failed: 502")
    assert FailureEvidence.of(_job("failed")) == FailureEvidence()
