"""Where the controller's adapter reads and acts on executions, per kind."""

from __future__ import annotations

import pytest

from awx_test_support import RecordingController
from untaped_awx.domain import Job
from untaped_awx.domain.jobs.job import JOB_KINDS
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.controller.jobs import JOB_PATHS


def _page(*records: dict[str, object]) -> dict[str, object]:
    return {"results": list(records), "next": None}


def test_every_execution_kind_has_its_paths() -> None:
    assert JOB_PATHS.keys() == JOB_KINDS.keys()
    assert {kind for kind, paths in JOB_PATHS.items() if paths.events is None} == {
        kind for kind, capability in JOB_KINDS.items() if not capability.events
    }


@pytest.mark.parametrize(
    ("kind", "collection"),
    [
        ("job", "jobs/"),
        ("workflow_job", "workflow_jobs/"),
        ("project_update", "project_updates/"),
        ("inventory_update", "inventory_updates/"),
        ("ad_hoc_command", "ad_hoc_commands/"),
    ],
)
def test_each_kind_lists_its_own_collection(kind: str, collection: str) -> None:
    api = RecordingController(answers=[_page({"id": 1})])
    assert [record["id"] for record in api.jobs(kind=kind, limit=20)] == [1]
    assert api.listings == [(collection, {"order_by": "-id"}, 20)]


def test_jobs_are_newest_first_unless_the_caller_orders_them() -> None:
    api = RecordingController()
    list(api.jobs(kind="job", params={"status": "successful"}))
    list(api.jobs(kind="job", params={"order_by": "started", "status": "running"}))
    assert [params for _, params, _ in api.listings] == [
        {"order_by": "-id", "status": "successful"},
        {"order_by": "started", "status": "running"},
    ]


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(AwxApiError, match="unsupported execution kind: nope"):
        RecordingController().job(kind="nope", job_id=1)


def test_one_job_is_read_from_its_collection() -> None:
    api = RecordingController(answers=[{"id": 42, "status": "successful"}])
    assert api.job(kind="job", job_id=42) == {"id": 42, "status": "successful"}
    api.job(kind="workflow_job", job_id=9)
    assert [path for _, path, _ in api.json_calls] == ["jobs/42/", "workflow_jobs/9/"]


def test_host_summaries_are_one_listing_failed_hosts_first() -> None:
    api = RecordingController(answers=[_page({"host_name": "web1", "ok": 1})])
    job = Job(id=7, kind="job", status="failed")
    assert list(api.job_host_summaries(job)) == [{"host_name": "web1", "ok": 1}]
    list(api.job_host_summaries(job, {"changed__gt": "0"}))
    assert api.listings == [
        ("jobs/7/job_host_summaries/", {"order_by": "-failed,host_name"}, None),
        ("jobs/7/job_host_summaries/", {"order_by": "-failed,host_name", "changed__gt": "0"}, None),
    ]


def test_actions_on_executions() -> None:
    api = RecordingController(answers=[{}, {}, {}, {"id": 8}, {"id": 9}])
    api.cancel_job(kind="project_update", job_id=3)
    api.decide_approval(4, approve=True)
    api.decide_approval(5, approve=False)
    assert api.relaunch_job(kind="job", job_id=6) == {"id": 8}
    assert api.relaunch_job(kind="job", job_id=6, hosts="failed") == {"id": 9}
    list(api.workflow_job_nodes(Job(id=2, kind="workflow_job", status="successful")))
    assert [(method, path) for method, path, _ in api.json_calls] == [
        ("POST", "project_updates/3/cancel/"),
        ("POST", "workflow_approvals/4/approve/"),
        ("POST", "workflow_approvals/5/deny/"),
        ("POST", "jobs/6/relaunch/"),
        ("POST", "jobs/6/relaunch/"),
        ("GET", "workflow_jobs/2/workflow_nodes/"),
    ]
    assert api.bodies[3:5] == [{}, {"hosts": "failed"}]


def test_a_log_downloads_whole() -> None:
    api = RecordingController(texts=["a\nb\n"])
    assert api.job_stdout(Job(id=7, kind="ad_hoc_command", status="running")) == "a\nb\n"
    assert api.text_calls == [("GET", "ad_hoc_commands/7/stdout/", {"format": "txt_download"})]


def test_events_live_under_each_kinds_own_listing() -> None:
    api = RecordingController()
    list(api.job_events(Job(id=7, kind="job", status="running"), {"counter__gt": "0"}))
    api.job_events_page(Job(id=8, kind="project_update", status="running"), {"page_size": "4"})
    assert api.json_calls == [
        ("GET", "jobs/7/job_events/", {"counter__gt": "0"}),
        ("GET", "project_updates/8/events/", {"page_size": "4"}),
    ]


def test_a_workflow_job_has_no_events() -> None:
    with pytest.raises(AwxApiError, match="workflow_job does not expose events"):
        RecordingController().job_events_page(Job(id=7, kind="workflow_job", status="running"), {})
