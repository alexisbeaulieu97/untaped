"""Real execution-kind responses and strict Controller monitoring route contracts."""

import json
from typing import Any

import httpx
import pytest

from untaped.capabilities.awx.application import WatchJob
from untaped.capabilities.awx.cli import _parallel, app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def seed_templates(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed("job_templates", id=50, name="sliced", organization=1, job_slice_count=2)
    fake.seed("job_templates", id=51, name="ordinary", organization=1)


@pytest.mark.parametrize("status", ["successful", "failed", "canceled", "error"])
def test_sliced_launch_wait_keeps_mixed_execution_kinds(
    fake_aap: Any,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    seed_templates(fake_aap)
    fake_aap.seed("workflow_jobs", id=101, name="sliced", status=status)
    fake_aap.router.routes.clear()
    route = fake_aap.router.post("https://aap.example.com/api/v2/job_templates/50/launch/").respond(
        201, json={"id": 101, "type": "workflow_job", "status": "pending"}
    )
    fake_aap.install(fake_aap.router)
    monkeypatch.setattr(
        _parallel, "WatchJob", lambda client: WatchJob(client, sleep=lambda _: None)
    )
    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "sliced", "ordinary", "--wait", "--format", "json"]
    )
    assert result.exit_code == (0 if status == "successful" else 1), result.output
    rows = json.loads(result.stdout)
    assert [row["target_id"] for row in rows] == [50, 51]
    assert [row["kind"] for row in rows] == ["workflow_job", "job"]
    assert rows[0]["id"] == 101
    assert rows[0]["status"] == status
    assert rows[1]["action"] == "completed"
    assert route.call_count == 1
    paths = [
        call.request.url.path for call in fake_aap.router.calls if call.request.method == "GET"
    ]
    assert "/api/v2/workflow_jobs/101/" in paths
    assert "/api/v2/jobs/101/" not in paths


@pytest.mark.parametrize(
    "response",
    [
        {"id": 101, "type": "unexpected", "status": "pending"},
        {"id": 101, "status": "pending"},
        {"id": 101, "type": "workflow_job"},
    ],
)
@pytest.mark.parametrize("continue_", [False, True])
def test_invalid_response_keeps_execution_id_without_retry_or_monitoring(
    fake_aap: Any,
    response: dict[str, Any],
    continue_: bool,
) -> None:
    seed_templates(fake_aap)
    fake_aap.router.routes.clear()
    route = fake_aap.router.post("https://aap.example.com/api/v2/job_templates/50/launch/").respond(
        201, json=response
    )
    fake_aap.install(fake_aap.router)
    args = ["job-templates", "launch", "sliced", "ordinary", "--track", "--format", "json"]
    if continue_:
        args.append("--continue-on-error")
    result = CliInvoker().invoke(app, args)
    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["id"] == 101
    assert rows[0]["target_id"] == 50
    assert rows[0]["kind"] == ("workflow_job" if response.get("type") == "workflow_job" else None)
    assert [row["action"] for row in rows] == ["failed", "completed" if continue_ else "skipped"]
    assert route.call_count == 1
    assert not any(
        "/101/" in call.request.url.path
        for call in fake_aap.router.calls
        if call.request.method == "GET"
    )


@pytest.mark.parametrize(
    "command,action,name,kind",
    [
        ("job-templates", "launch", "sliced", "workflow_job"),
        ("job-templates", "launch", "ordinary", "job"),
        ("workflow-templates", "launch", "workflow", "workflow_job"),
        ("projects", "sync", "project", "project_update"),
        ("inventory-sources", "sync", "source", "inventory_update"),
        ("inventories", "sync", "inventory", "inventory_update"),
    ],
)
@pytest.mark.parametrize("status", ["successful", "failed", "canceled", "error"])
@pytest.mark.parametrize("also_wait", [False, True])
def test_track_pending_to_terminal_uses_only_supported_routes(
    fake_aap: Any,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    action: str,
    name: str,
    kind: str,
    status: str,
    also_wait: bool,
) -> None:
    from untaped.capabilities.awx.cli import _context
    from untaped.capabilities.awx.infrastructure.job_monitor import PollingJobMonitor

    seed_templates(fake_aap)
    fake_aap.seed("workflow_job_templates", id=60, name="workflow", organization=1)
    fake_aap.seed("projects", id=70, name="project", organization=1, scm_type="git")
    fake_aap.seed("inventories", id=80, name="inventory", organization=1, kind="")
    fake_aap.seed("inventory_sources", id=90, name="source", inventory=80, source="ec2")
    collection = f"{kind}s"
    fake_aap.seed(collection, id=101, status=status, type=kind)
    fake_aap.router.routes.clear()
    route = fake_aap.router.post(url__regex=r".*/(?:launch|update)/").respond(
        201, json={"id": 101, "type": kind, "status": "pending"}
    )
    fake_aap.install(fake_aap.router)
    monkeypatch.setattr(
        _context,
        "PollingJobMonitor",
        lambda client: PollingJobMonitor(client, sleep=lambda _: None),
    )
    args = [command, action, name, "--track", "--format", "json"]
    if also_wait:
        args.append("--wait")
    result = CliInvoker().invoke(app, args)
    assert result.exit_code == (0 if status == "successful" else 1), result.output
    row = json.loads(result.stdout)[0]
    assert row["id"] == 101
    assert row["status"] == status
    assert row["action"] == ("completed" if status == "successful" else "failed")
    assert route.call_count == 1
    paths = [
        c.request.url.path
        for c in fake_aap.router.calls
        if f"/{collection}/101/" in c.request.url.path
    ]
    detail = f"/api/v2/{collection}/101/"
    if kind == "workflow_job":
        assert paths and set(paths) == {detail}
        assert "pending" in result.stderr and status in result.stderr
    else:
        events = "job_events" if kind == "job" else "events"
        assert set(paths) == {detail, f"{detail}{events}/"}


@pytest.mark.parametrize("subpath", ["job_events", "events", "stdout"])
def test_fake_rejects_invented_workflow_routes(fake_aap: Any, subpath: str) -> None:
    fake_aap.seed("workflow_jobs", id=101, status="successful")
    response = httpx.get(f"https://aap.example.com/api/v2/workflow_jobs/101/{subpath}/")
    assert response.status_code == 404


@pytest.mark.parametrize("collection", ["project_updates", "inventory_updates"])
def test_fake_rejects_invented_update_job_events(fake_aap: Any, collection: str) -> None:
    fake_aap.seed(collection, id=101, status="successful")
    response = httpx.get(f"https://aap.example.com/api/v2/{collection}/101/job_events/")
    assert response.status_code == 404


@pytest.mark.parametrize("command", ["events", "logs"])
def test_workflow_event_and_log_commands_fail_without_unsupported_requests(
    fake_aap: Any, command: str
) -> None:
    fake_aap.seed("workflow_jobs", id=101, status="successful")
    result = CliInvoker().invoke(app, ["jobs", command, "101", "--kind", "workflow_job"])
    assert result.exit_code == 1, result.output
    assert "workflow_job" in result.output and "not" in result.output
    assert [c.request.url.path for c in fake_aap.router.calls] == ["/api/v2/workflow_jobs/101/"]
