"""Fixed launch/sync selection, execution identities and terminal outcomes."""

import json
from typing import Any

import httpx
import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def seed(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed("inventories", id=20, name="prod", organization=1, kind="")
    fake.seed("inventory_sources", id=30, name="cloud", inventory=20, source="ec2")
    fake.seed("projects", id=40, name="playbooks", organization=1, scm_type="git")
    fake.seed("job_templates", id=50, name="deploy", organization=1)
    fake.seed("workflow_job_templates", id=60, name="pipeline", organization=1)


CASES = [
    ("job-templates", "launch", "deploy", "job_templates", 50, "job"),
    ("workflow-templates", "launch", "pipeline", "workflow_job_templates", 60, "workflow_job"),
    ("projects", "sync", "playbooks", "projects", 40, "project_update"),
    ("inventory-sources", "sync", "cloud", "inventory_sources", 30, "inventory_update"),
    ("inventories", "sync", "prod", "inventory_sources", 30, "inventory_update"),
]


@pytest.mark.parametrize("command,action,name,path,target,kind", CASES)
def test_actions_return_execution_and_target_identities(
    fake_aap: Any, command: str, action: str, name: str, path: str, target: int, kind: str
) -> None:
    seed(fake_aap)
    result = CliInvoker().invoke(app, [command, action, name, "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["target_id"] == target
    assert rows[0]["id"] != target
    assert rows[0]["kind"] == kind
    assert rows[0]["action"] == "completed"
    assert [(p, i, a) for p, i, a, _ in fake_aap.actions_called] == [
        (path, target, "launch" if action == "launch" else "update")
    ]


@pytest.mark.parametrize("command,action,name,path,target,kind", CASES)
def test_action_dry_run_selects_without_post(
    fake_aap: Any, command: str, action: str, name: str, path: str, target: int, kind: str
) -> None:
    seed(fake_aap)
    result = CliInvoker().invoke(app, [command, action, name, "--dry-run", "--format", "json"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)[0]
    assert row["target_id"] == target
    assert row["id"] is None
    assert row["action"] == "preview"
    assert fake_aap.actions_called == []


@pytest.mark.parametrize("command,action", [(c, a) for c, a, *_ in CASES])
def test_actions_require_explicit_selection(fake_aap: Any, command: str, action: str) -> None:
    seed(fake_aap)
    result = CliInvoker().invoke(app, [command, action])
    assert result.exit_code != 0
    assert fake_aap.actions_called == []


@pytest.mark.parametrize("command,action", [(c, a) for c, a, *_ in CASES])
def test_actions_reject_empty_selection(fake_aap: Any, command: str, action: str) -> None:
    seed(fake_aap)
    result = CliInvoker().invoke(app, [command, action, "--stdin"], input="")
    assert result.exit_code != 0
    assert fake_aap.actions_called == []


def test_launch_query_and_deduplicated_typed_id_pipe(fake_aap: Any) -> None:
    seed(fake_aap)
    selected = CliInvoker().invoke(
        app,
        ["job-templates", "list", "--filter", "id=50", "--search", "deploy", "--format", "pipe"],
    )
    assert selected.exit_code == 0, selected.output
    fake_aap.store["job_templates"][50]["name"] = "renamed"
    launched = CliInvoker().invoke(
        app, ["job-templates", "launch", "--stdin", "--format", "json"], input=selected.stdout * 2
    )
    assert launched.exit_code == 0, launched.output
    assert [i for _, i, _, _ in fake_aap.actions_called] == [50]


@pytest.mark.parametrize("flag", ["--wait", "--track"])
@pytest.mark.parametrize("status", ["failed", "canceled", "error"])
@pytest.mark.parametrize("command,action,name,path,target,kind", CASES)
def test_action_terminal_failures_are_nonzero(
    fake_aap: Any,
    command: str,
    action: str,
    name: str,
    path: str,
    target: int,
    kind: str,
    flag: str,
    status: str,
) -> None:
    seed(fake_aap)
    fake_aap.next_action_status = status
    result = CliInvoker().invoke(app, [command, action, name, flag, "--format", "json"])
    assert result.exit_code == 1, result.output
    row = json.loads(result.stdout)[0]
    assert row["status"] == status
    assert row["id"] != target
    assert row["action"] == "failed"


@pytest.mark.parametrize("status", ["failed", "canceled", "error"])
def test_jobs_wait_failed_terminal_is_nonzero(fake_aap: Any, status: str) -> None:
    fake_aap.seed("jobs", id=42, status=status)
    result = CliInvoker().invoke(app, ["jobs", "wait", "42", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)[0]["status"] == status


def test_projects_update_removed(fake_aap: Any) -> None:
    seed(fake_aap)
    result = CliInvoker().invoke(app, ["projects", "update", "playbooks"])
    assert result.exit_code != 0
    assert fake_aap.actions_called == []


@pytest.mark.parametrize("kind,sources", [("smart", True), ("", False)])
def test_inventory_sync_unsupported_or_no_source_is_explicit(
    fake_aap: Any, kind: str, sources: bool
) -> None:
    seed(fake_aap)
    fake_aap.store["inventories"][20]["kind"] = kind
    if not sources:
        fake_aap.store["inventory_sources"].clear()
    result = CliInvoker().invoke(app, ["inventories", "sync", "prod"])
    assert result.exit_code != 0
    assert "smart" in result.output or "source" in result.output
    assert fake_aap.actions_called == []


def test_inventory_sync_freezes_all_sources_before_first_post(fake_aap: Any) -> None:
    seed(fake_aap)
    fake_aap.seed("inventory_sources", id=31, name="other", inventory=20, source="scm")
    calls: list[int] = []

    def launch(request: httpx.Request) -> httpx.Response:
        target = int(request.url.path.split("/")[-3])
        calls.append(target)
        fake_aap.seed("inventory_sources", id=32, name="late", inventory=20, source="ec2")
        return httpx.Response(202, json={"id": 100 + target, "status": "pending"})

    fake_aap.router.routes.clear()
    fake_aap.router.post(url__regex=r".*/inventory_sources/\d+/update/").mock(side_effect=launch)
    fake_aap.install(fake_aap.router)
    result = CliInvoker().invoke(app, ["inventories", "sync", "prod", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert calls == [30, 31]
    assert [r["kind"] for r in json.loads(result.stdout)] == [
        "inventory_update",
        "inventory_update",
    ]


@pytest.mark.parametrize("continue_", [False, True])
def test_launch_runtime_failure_retains_success_and_skips_without_retry(
    fake_aap: Any, continue_: bool
) -> None:
    seed(fake_aap)
    fake_aap.seed("job_templates", id=51, name="bad", organization=1)
    fake_aap.seed("job_templates", id=52, name="last", organization=1)
    fake_aap.router.routes.clear()
    route = fake_aap.router.post("https://aap.example.com/api/v2/job_templates/51/launch/").mock(
        side_effect=httpx.ReadTimeout("ambiguous POST")
    )
    fake_aap.install(fake_aap.router)
    args = ["job-templates", "launch", "deploy", "bad", "last", "--format", "json"]
    if continue_:
        args.append("--continue-on-error")
    result = CliInvoker().invoke(app, args)
    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert [r["action"] for r in rows] == [
        "completed",
        "failed",
        "completed" if continue_ else "skipped",
    ]
    assert rows[0]["id"] is not None
    assert rows[1]["id"] is None
    assert rows[1]["target_id"] == 51
    assert route.call_count == 1


def test_parallel_launch_stops_new_submissions_but_retains_inflight(fake_aap: Any) -> None:
    import threading

    seed(fake_aap)
    for id_, name in [(51, "bad"), (52, "last")]:
        fake_aap.seed("job_templates", id=id_, name=name, organization=1)
    barrier = threading.Barrier(2, timeout=5)
    failure_returned = threading.Event()
    calls: list[int] = []

    def launch(request: httpx.Request) -> httpx.Response:
        target = int(request.url.path.split("/")[-3])
        calls.append(target)
        barrier.wait()
        if target == 51:
            failure_returned.set()
            return httpx.Response(400, json={"detail": "rejected"})
        failure_returned.wait(5)
        # Hold the other in-flight POST until the first error has reached the
        # scheduler, making the expected skipped third target deterministic.
        import time

        time.sleep(0.05)
        return httpx.Response(202, json={"id": 100, "type": "job", "status": "pending"})

    fake_aap.router.routes.clear()
    fake_aap.router.post(url__regex=r".*/job_templates/\d+/launch/").mock(side_effect=launch)
    fake_aap.install(fake_aap.router)
    result = CliInvoker().invoke(
        app,
        ["job-templates", "launch", "deploy", "bad", "last", "--parallel", "2", "--format", "json"],
    )
    assert result.exit_code == 1, result.output
    assert sorted(calls) == [50, 51]
    assert [r["action"] for r in json.loads(result.stdout)] == ["completed", "failed", "skipped"]


def test_launch_error_redacts_extra_var_values(fake_aap: Any) -> None:
    seed(fake_aap)
    fake_aap.router.routes.clear()
    fake_aap.router.post(url__regex=r".*/job_templates/\d+/launch/").respond(
        400, json={"detail": "bad value secret-value"}
    )
    fake_aap.install(fake_aap.router)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "launch",
            "deploy",
            "--extra-vars",
            "password=secret-value",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 1
    assert "secret-value" not in result.output
    assert "[REDACTED]" in result.output or "<redacted>" in result.output


@pytest.mark.parametrize("command,action,name,path,target,kind", CASES)
def test_action_pipe_wait_uses_execution_id(
    fake_aap: Any, command: str, action: str, name: str, path: str, target: int, kind: str
) -> None:
    seed(fake_aap)
    launched = CliInvoker().invoke(app, [command, action, name, "--format", "pipe"])
    assert launched.exit_code == 0, launched.output
    waited = CliInvoker().invoke(
        app, ["jobs", "wait", "--stdin", "--kind", kind, "--format", "json"], input=launched.stdout
    )
    assert waited.exit_code == 0, waited.output
    assert json.loads(waited.stdout)[0]["id"] != target


@pytest.mark.parametrize("command,action,name,path,target,kind", CASES)
def test_action_invalid_name_in_selection_prevents_all_posts(
    fake_aap: Any, command: str, action: str, name: str, path: str, target: int, kind: str
) -> None:
    seed(fake_aap)
    result = CliInvoker().invoke(app, [command, action, name, "ghost", "--continue-on-error"])
    assert result.exit_code != 0
    assert fake_aap.actions_called == []


def test_monitoring_concurrency_is_bounded() -> None:
    import threading
    import time

    from rich.console import Console

    from untaped.capabilities.awx.cli._parallel import _drain_parallel
    from untaped.capabilities.awx.domain import Job

    lock = threading.Lock()
    active = 0
    peak = 0

    class Monitor:
        def fetch(self, job: Job) -> Job:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(active, peak)
            time.sleep(0.03)
            with lock:
                active -= 1
            return job.model_copy(update={"status": "successful"})

        def stream_events(self, job: Job, **kwargs: Any) -> list[Any]:
            return []

    jobs = [(str(i), Job(id=i, kind="job", status="successful")) for i in range(1, 26)]
    results, errors = _drain_parallel(Monitor(), jobs, Console())
    assert not errors
    assert len(results) == 25
    assert peak <= 10


@pytest.mark.parametrize(
    "invalid", ["manual-project", "manual-source", "smart-inventory", "empty-inventory"]
)
def test_sync_known_invalid_batch_never_starts_valid_target(fake_aap: Any, invalid: str) -> None:
    seed(fake_aap)
    if invalid == "manual-project":
        fake_aap.seed("projects", id=41, name="invalid", organization=1, scm_type="")
        command = "projects"
    elif invalid == "manual-source":
        fake_aap.seed("inventory_sources", id=31, name="invalid", inventory=20, source="")
        command = "inventory-sources"
    else:
        fake_aap.seed(
            "inventories",
            id=21,
            name="invalid",
            organization=1,
            kind="smart" if invalid == "smart-inventory" else "",
        )
        command = "inventories"
    result = CliInvoker().invoke(app, [command, "sync", "--all", "--continue-on-error"])
    assert result.exit_code != 0
    assert "invalid" in result.output
    assert fake_aap.actions_called == []


def test_constructed_inventory_sync_uses_managed_source(fake_aap: Any) -> None:
    seed(fake_aap)
    fake_aap.store["inventories"][20]["kind"] = "constructed"
    fake_aap.seed("constructed_inventories", id=20, name="prod", organization=1, kind="constructed")
    fake_aap.store["inventory_sources"][30]["source"] = "constructed"
    result = CliInvoker().invoke(app, ["inventories", "sync", "prod", "--wait", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert [(p, i, a) for p, i, a, _ in fake_aap.actions_called] == [
        ("inventory_sources", 30, "update")
    ]


def test_scoped_source_query_selects_only_matching_inventory(fake_aap: Any) -> None:
    seed(fake_aap)
    fake_aap.seed("inventories", id=21, name="other", organization=1, kind="")
    fake_aap.seed("inventory_sources", id=31, name="cloud", inventory=21, source="ec2")
    result = CliInvoker().invoke(
        app,
        [
            "inventory-sources",
            "sync",
            "--filter",
            "source=ec2",
            "--search",
            "cloud",
            "--inventory",
            "prod",
            "--inventory-org",
            "Default",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [i for _, i, _, _ in fake_aap.actions_called] == [30]


def test_source_sync_scoped_typed_pipe_cannot_escape_inventory(fake_aap: Any) -> None:
    seed(fake_aap)
    fake_aap.seed("inventories", id=21, name="other", organization=1, kind="")
    selected = CliInvoker().invoke(app, ["inventory-sources", "list", "--all", "--format", "pipe"])
    result = CliInvoker().invoke(
        app, ["inventory-sources", "sync", "--stdin", "--inventory", "other"], input=selected.stdout
    )
    assert result.exit_code != 0
    assert fake_aap.actions_called == []


def test_monitor_identity_does_not_collide_with_literal_resource_names(fake_aap: Any) -> None:
    seed(fake_aap)
    fake_aap.seed("job_templates", id=51, name="deploy", organization=1)
    fake_aap.seed("job_templates", id=52, name="deploy#50", organization=1)
    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "--all", "--wait", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["target_id"] for r in rows] == [50, 51, 52]
    assert all(r["action"] == "completed" for r in rows)
