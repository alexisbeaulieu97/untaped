"""Fixed launch/sync selection, execution identities and terminal outcomes."""

import json
from typing import Any

import httpx
import pytest

from untaped.capabilities.awx.cli import app, parallel
from untaped.testing import CliInvoker, ScriptedPromptBackend

pytestmark = pytest.mark.integration


def seed(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed("inventories", id=20, name="prod", organization=1, kind="")
    fake.seed("inventory_sources", id=30, name="cloud", inventory=20, source="ec2")
    fake.seed("projects", id=40, name="playbooks", organization=1, scm_type="git")
    fake.seed("job_templates", id=50, name="deploy", organization=1, ask_variables_on_launch=True)
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
    assert row["action"] == "planned"
    assert fake_aap.actions_called == []


@pytest.mark.parametrize("command,action,name,path,target,kind", CASES)
def test_action_rows_are_outcomes_that_jobs_accept(
    fake_aap: Any, command: str, action: str, name: str, path: str, target: int, kind: str
) -> None:
    """Launch/sync rows pipe as ``awx.<action>_outcome``, never as ``awx.job``."""
    seed(fake_aap)
    result = CliInvoker().invoke(app, [command, action, name, "--format", "pipe"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.splitlines()[0])
    assert envelope["kind"] == f"awx.{action}_outcome"

    fake_aap.seed(f"{kind}s", id=envelope["record"]["id"], name=name, status="successful")
    waited = CliInvoker().invoke(
        app, ["jobs", "wait", "--stdin", "--format", "json"], input=result.stdout
    )
    assert waited.exit_code == 0, waited.output
    assert json.loads(waited.stdout)[0]["kind"] == kind


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
        app,
        ["job-templates", "launch", "--yes", "--stdin", "--format", "json"],
        input=selected.stdout * 2,
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


def test_inventory_sync_lists_sources_for_every_inventory_in_one_request(fake_aap: Any) -> None:
    seed(fake_aap)
    fake_aap.seed("inventories", id=21, name="stage", organization=1, kind="")
    fake_aap.seed("inventory_sources", id=33, name="stage-cloud", inventory=21, source="ec2")
    fake_aap.seed("inventory_sources", id=31, name="other", inventory=20, source="scm")

    result = CliInvoker().invoke(app, ["inventories", "sync", "--all", "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert [row["target_id"] for row in json.loads(result.stdout)] == [30, 31, 33]
    source_lists = [
        call.request
        for call in fake_aap.router.calls
        if call.request.method == "GET" and call.request.url.path.endswith("/inventory_sources/")
    ]
    assert len(source_lists) == 1
    assert source_lists[0].url.params["inventory__in"] == "20,21"


def test_inventory_sync_names_the_inventory_without_sources(fake_aap: Any) -> None:
    seed(fake_aap)
    fake_aap.seed("inventories", id=21, name="empty", organization=1, kind="")

    result = CliInvoker().invoke(app, ["inventories", "sync", "--all", "--yes"])

    assert result.exit_code != 0
    assert "'empty' (id=21): no inventory sources to sync" in result.output
    assert fake_aap.actions_called == []


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
    args = ["job-templates", "launch", "--yes", "deploy", "bad", "last", "--format", "json"]
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
        [
            "job-templates",
            "launch",
            "--yes",
            "deploy",
            "bad",
            "last",
            "--parallel",
            "2",
            "--format",
            "json",
        ],
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

    from untaped.capabilities.awx.cli.parallel import drain_parallel
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
    results, errors = drain_parallel(Monitor(), jobs, Console().print)
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
    result = CliInvoker().invoke(app, [command, "sync", "--yes", "--all", "--continue-on-error"])
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
            "--yes",
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
        app, ["job-templates", "launch", "--yes", "--all", "--wait", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["target_id"] for r in rows] == [50, 51, 52]
    assert all(r["action"] == "completed" for r in rows)


@pytest.mark.parametrize(
    "command,args",
    [
        ("job-templates", ["launch", "--all"]),
        ("job-templates", ["launch", "--search", "deploy"]),
        ("job-templates", ["launch", "--filter", "name=deploy"]),
        ("projects", ["sync", "--all"]),
    ],
)
@pytest.mark.parametrize("answer", [True, False])
def test_mass_actions_preview_and_confirm(
    fake_aap: Any, command: str, args: list[str], answer: bool
) -> None:
    seed(fake_aap)
    backend = ScriptedPromptBackend(confirms=[answer])
    result = CliInvoker().invoke(app, [command, *args], interactive=True, prompt_backend=backend)
    assert result.exit_code == (0 if answer else 1), result.output + result.stderr
    if not answer:
        assert "cancelled; no changes made" in result.stderr
    assert len(backend.calls) == 1
    assert bool(fake_aap.actions_called) is answer
    assert "id=" in result.stderr  # preview lists the targets before asking


def test_multi_name_launch_requires_confirmation_without_terminal(fake_aap: Any) -> None:
    seed(fake_aap)
    fake_aap.seed("job_templates", id=51, name="other", organization=1)
    result = CliInvoker().invoke(app, ["job-templates", "launch", "deploy", "other"])
    assert result.exit_code != 0
    assert "--yes or --dry-run" in result.stderr
    assert fake_aap.actions_called == []


def test_mass_launch_yes_skips_prompt(fake_aap: Any) -> None:
    seed(fake_aap)
    backend = ScriptedPromptBackend(confirms=[])
    result = CliInvoker().invoke(
        app,
        ["job-templates", "launch", "--all", "--yes"],
        interactive=True,
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output
    assert backend.calls == []
    assert len(fake_aap.actions_called) == 1


def test_single_named_launch_does_not_prompt(fake_aap: Any) -> None:
    seed(fake_aap)
    backend = ScriptedPromptBackend(confirms=[])
    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "deploy"], interactive=True, prompt_backend=backend
    )
    assert result.exit_code == 0, result.output
    assert backend.calls == []


@pytest.mark.parametrize("flag", ["--wait", "--track"])
@pytest.mark.parametrize(
    ("command", "name"), [("job-templates launch", "deploy"), ("projects sync", "playbooks")]
)
def test_timeout_stops_waiting_and_names_the_running_execution(
    fake_aap: Any, flag: str, command: str, name: str
) -> None:
    seed(fake_aap)
    fake_aap.next_action_status = "running"

    result = CliInvoker().invoke(
        app, [*command.split(), name, flag, "--timeout", "0", "--format", "json"]
    )

    assert result.exit_code == 1, result.output
    row = json.loads(result.stdout)[0]
    assert (row["status"], row["action"]) == ("running", "failed")
    assert row["detail"] == "still running after --timeout 0s; it keeps running"
    kind = row["kind"]
    assert f"hint: run `untaped awx jobs wait {row['id']} --kind {kind}`" in result.stderr


@pytest.mark.parametrize("command", ["job-templates launch deploy", "projects sync playbooks"])
def test_timeout_needs_wait_or_track_and_a_non_negative_value(fake_aap: Any, command: str) -> None:
    seed(fake_aap)
    for extra in (["--timeout", "5"], ["--wait", "--timeout", "-1"]):
        result = CliInvoker().invoke(app, [*command.split(), *extra])
        assert result.exit_code == 2, result.output
    assert fake_aap.actions_called == []


@pytest.mark.parametrize("flag", ["--wait", "--track"])
def test_ctrl_c_while_waiting_stops_promptly_and_names_running_jobs(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    """Ctrl-C must not block until every launched execution finishes."""
    import queue
    import sys
    import threading
    import time

    seed(fake_aap)
    fake_aap.next_action_status = "running"
    # Safety net: if polling ignored the interrupt, the job ends after 3s
    # and the elapsed-time assertion below fails instead of hanging.
    timer = threading.Timer(
        3.0, lambda: [job.update(status="successful") for job in fake_aap.list_records("jobs")]
    )
    timer.start()
    real_get = queue.Queue.get

    def interrupt() -> None:
        raise KeyboardInterrupt

    def interrupted_get(self: Any, *args: Any, **kwargs: Any) -> Any:
        if sys._getframe(1).f_code.co_filename.endswith("parallel.py"):
            raise KeyboardInterrupt
        return real_get(self, *args, **kwargs)

    # Ctrl-C lands on the main thread while workers poll: in the idle wait
    # (``--wait``) or the event-queue drain (``--track``).
    monkeypatch.setattr(parallel, "idle", interrupt)
    monkeypatch.setattr(queue.Queue, "get", interrupted_get)
    started = time.monotonic()
    try:
        result = CliInvoker().invoke(app, ["job-templates", "launch", "deploy", flag])
    finally:
        timer.cancel()
    elapsed = time.monotonic() - started

    assert elapsed < 2.5
    assert result.exit_code == 130, result.output
    job_id = next(iter(fake_aap.store["jobs"]))
    assert f"untaped awx jobs wait {job_id} --kind job" in result.stderr


def test_ctrl_c_while_waiting_lists_only_executions_still_running(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    seed(fake_aap)
    fake_aap.seed("job_templates", id=51, name="other", organization=1)
    fake_aap.next_action_status = "running"  # first launch only; the second finishes
    timer = threading.Timer(
        3.0, lambda: [job.update(status="successful") for job in fake_aap.list_records("jobs")]
    )
    timer.start()

    def interrupt() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(parallel, "idle", interrupt)
    try:
        result = CliInvoker().invoke(
            app, ["job-templates", "launch", "deploy", "other", "--yes", "--wait"]
        )
    finally:
        timer.cancel()

    assert result.exit_code == 130, result.output
    running, finished = (job["id"] for job in fake_aap.list_records("jobs"))
    assert f"job {running} keeps running" in result.stderr
    assert f"job {finished} " not in result.stderr
    assert f"untaped awx jobs wait {running} --kind job" in result.stderr


def test_ctrl_c_during_submission_names_executions_already_submitted(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.capabilities.awx.application import RunAction

    seed(fake_aap)
    fake_aap.seed("job_templates", id=51, name="other", organization=1)
    fake_aap.next_action_status = "pending"
    real_execute = RunAction.execute
    calls: list[int] = []

    def interrupt_second(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real_execute(self, *args, **kwargs)

    monkeypatch.setattr(RunAction, "execute", interrupt_second)
    result = CliInvoker().invoke(app, ["job-templates", "launch", "deploy", "other", "--yes"])

    assert result.exit_code == 130, result.output
    (job,) = fake_aap.list_records("jobs")
    assert f"job {job['id']} keeps running" in result.stderr
    assert f"untaped awx jobs wait {job['id']} --kind job" in result.stderr
