"""Job terminal states and the AWX event shapes the models must accept."""

from __future__ import annotations

import pytest

from untaped.capabilities.awx.domain import Job, JobEvent
from untaped.capabilities.awx.domain.job import HostSummary, host_summaries


@pytest.mark.parametrize(
    ("status", "terminal"),
    [
        ("new", False),
        ("pending", False),
        ("waiting", False),
        ("running", False),
        ("successful", True),
        ("failed", True),
        ("error", True),
        ("canceled", True),
    ],
)
def test_is_terminal(status: str, terminal: bool) -> None:
    assert Job(id=1, kind="job", status=status).is_terminal is terminal


def test_job_ignores_unknown_fields() -> None:
    job = Job.model_validate({"id": 1, "kind": "job", "status": "running", "elapsed": 12.3})
    assert job.status == "running"


def test_job_event_accepts_int_host_with_separate_host_name() -> None:
    """Regression: AWX returns ``host`` as an FK id and ``host_name`` as the
    denormalised string; ``host: str | None`` rejected every host event."""
    ev = JobEvent.model_validate(
        {"counter": 5, "event": "runner_on_ok", "host": 7, "host_name": "web-01"}
    )
    assert (ev.host, ev.host_name) == (7, "web-01")


def test_job_event_keeps_the_module_result_out_of_rendered_rows() -> None:
    ev = JobEvent.model_validate(
        {
            "counter": 9,
            "event": "runner_on_failed",
            "event_data": {"res": {"msg": "boom", "rc": 2}, "ignore_errors": False},
        }
    )
    assert ev.res == {"msg": "boom", "rc": 2}
    assert "res" not in ev.model_dump()
    assert JobEvent.model_validate({"counter": 1, "event_data": {}}).res is None


def test_job_carries_the_ref_and_commit_it_ran() -> None:
    job = Job.model_validate(
        {"id": 1, "kind": "job", "status": "running", "scm_branch": "fix", "scm_revision": "abc"}
    )
    assert (job.scm_branch, job.scm_revision) == ("fix", "abc")


def test_job_carries_why_it_ended_but_leaves_it_out_of_rows() -> None:
    job = Job.model_validate(
        {
            "id": 1,
            "kind": "job",
            "status": "error",
            "job_explanation": "Job terminated due to error",
            "result_traceback": "Traceback …\nRuntimeError: pod failed",
        }
    )
    assert job.job_explanation == "Job terminated due to error"
    assert job.result_traceback == "Traceback …\nRuntimeError: pod failed"
    assert {"job_explanation", "result_traceback"}.isdisjoint(job.model_dump())


def test_job_event_lines_are_its_stdout_without_ansi_colours() -> None:
    ev = JobEvent.model_validate(
        {
            "counter": 3,
            "event": "runner_on_failed",
            "stdout": "\x1b[0;31mfatal: [web1]: FAILED! => {}\x1b[0m\r\n\x1b[1;35mhint\x1b[0m",
        }
    )
    assert ev.lines == ["fatal: [web1]: FAILED! => {}", "hint"]
    assert JobEvent(counter=4).lines == []


def test_a_host_summary_names_awx_counters_as_the_recap_does() -> None:
    record = {
        "host_name": "web1",
        "ok": 12,
        "changed": 3,
        "failures": 1,
        "dark": 2,
        "skipped": 4,
        "rescued": 5,
        "ignored": 6,
        "processed": 1,
        "failed": True,
    }
    assert HostSummary.from_record(record).model_dump() == {
        "ok": 12,
        "changed": 3,
        "failed": 1,
        "unreachable": 2,
        "skipped": 4,
        "rescued": 5,
        "ignored": 6,
    }
    assert HostSummary.from_record({"host_name": "db1"}).model_dump() == dict.fromkeys(
        ("ok", "changed", "failed", "unreachable", "skipped", "rescued", "ignored"), 0
    )


def test_host_summaries_keep_the_first_500_hosts_and_say_so() -> None:
    records = ({"host_name": f"h{index:03}", "ok": 1} for index in range(600))
    hosts, truncated = host_summaries(records)
    assert (len(hosts), truncated) == (500, True)
    assert hosts["h000"] == HostSummary(ok=1)

    hosts, truncated = host_summaries([{"host_name": "web1", "dark": 1}, {"host": 9}])
    assert (hosts, truncated) == ({"9": HostSummary(), "web1": HostSummary(unreachable=1)}, False)
    assert list(hosts) == ["9", "web1"]  # by name, whatever order they were read in
