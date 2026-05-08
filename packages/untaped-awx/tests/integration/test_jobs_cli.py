"""End-to-end CLI tests for the upgraded ``untaped awx jobs`` UX."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner
from untaped_awx import app

pytestmark = pytest.mark.integration


def _seed_running_job(fake: Any, *, job_id: int = 42) -> None:
    fake.seed(
        "jobs",
        id=job_id,
        name="deploy",
        status="successful",
        started="2026-01-01T00:00:00Z",
        finished="2026-01-01T00:01:00Z",
        stdout="line-0\nline-1\nline-2\n",
    )


def _seed_events(fake: Any, *, job_id: int = 42) -> None:
    fake.seed(
        "job_events",
        id=1,
        job=job_id,
        counter=1,
        event="playbook_on_play_start",
        play="Deploy",
    )
    fake.seed(
        "job_events",
        id=2,
        job=job_id,
        counter=2,
        event="playbook_on_task_start",
        task="install",
    )
    fake.seed(
        "job_events",
        id=3,
        job=job_id,
        counter=3,
        event="runner_on_ok",
        host=5,
        host_name="web-01",
        task="install",
    )
    fake.seed(
        "job_events",
        id=4,
        job=job_id,
        counter=4,
        event="runner_on_failed",
        host=6,
        host_name="api-01",
        task="install",
        failed=True,
    )


def test_jobs_list_returns_seeded_records(fake_aap: Any) -> None:
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    result = CliRunner().invoke(app, ["jobs", "list", "--format", "raw", "--columns", "id"])
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines())
    assert ids == ["42", "43"]


def test_jobs_list_status_filter_passes_to_awx(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=1, name="ok", status="successful")
    fake_aap.seed("jobs", id=2, name="bad", status="failed")
    result = CliRunner().invoke(
        app, ["jobs", "list", "--status", "failed", "--format", "raw", "--columns", "id"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "2"


def test_jobs_events_drains_existing(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliRunner().invoke(
        app, ["jobs", "events", "42", "--format", "raw", "--columns", "counter"]
    )
    assert result.exit_code == 0, result.output
    counters = sorted(result.stdout.strip().splitlines())
    assert counters == ["1", "2", "3", "4"]


def test_jobs_events_follow_streams_events_to_stdout(fake_aap: Any) -> None:
    """Regression: ``--follow`` used to build the full row list before
    printing, so nothing appeared until the job hit terminal. The CLI
    now emits each event as it's yielded.

    Also pins the NDJSON contract: ``--follow --format json`` emits one
    bare JSON object per line so ``jq`` can ingest directly without
    ``jq -s '.[]'`` (matches ``kubectl get -w -o json``).
    """
    import json as _json

    _seed_running_job(fake_aap)  # already terminal — drain loop returns
    _seed_events(fake_aap)
    result = CliRunner().invoke(
        app,
        ["jobs", "events", "42", "--follow", "--format", "json", "--columns", "counter"],
    )
    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.strip().splitlines() if line]
    parsed = [_json.loads(line) for line in lines]
    # NDJSON: each line is a bare JSON object, NOT a single-element array.
    assert all(isinstance(p, dict) for p in parsed), parsed
    assert [p["counter"] for p in parsed] == [1, 2, 3, 4]


def test_jobs_events_follow_with_table_format_renders_human_lines(fake_aap: Any) -> None:
    """Table mode under ``--follow`` streams one colored human-readable
    line per event (PLAY/TASK/ok/changed/failed), via Rich Console — ANSI
    on a TTY, plain text under ``CliRunner`` (which doesn't simulate one).
    """
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliRunner().invoke(app, ["jobs", "events", "42", "--follow"])
    assert result.exit_code == 0, result.output
    # ``CliRunner`` has no TTY, so colour is stripped — but the rendered
    # shape (PLAY/TASK/ok/failed) must still appear.
    out = result.stdout
    assert "PLAY [Deploy]" in out
    assert "TASK [install]" in out
    assert "ok: web-01" in out
    assert "failed: api-01" in out


def test_jobs_events_server_side_filter(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "jobs",
            "events",
            "42",
            "--filter",
            "event=runner_on_failed",
            "--format",
            "raw",
            "--columns",
            "host_name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "api-01"


def test_jobs_events_from_counter_skips_already_seen(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "jobs",
            "events",
            "42",
            "--from-counter",
            "2",
            "--format",
            "raw",
            "--columns",
            "counter",
        ],
    )
    assert result.exit_code == 0, result.output
    counters = sorted(result.stdout.strip().splitlines())
    assert counters == ["3", "4"]


def test_jobs_logs_prints_full_stdout_by_default(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    result = CliRunner().invoke(app, ["jobs", "logs", "42"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["line-0", "line-1", "line-2"]


def test_jobs_logs_supports_standard_raw_columns_options(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "--format", "raw", "--columns", "line"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["line-0", "line-1", "line-2"]


def test_jobs_logs_supports_structured_formatter_output(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    result = CliRunner().invoke(
        app, ["jobs", "logs", "42", "--format", "json", "--columns", "line"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ('[{"line": "line-0"}, {"line": "line-1"}, {"line": "line-2"}]')


def test_jobs_logs_tail_returns_only_last_n(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "--tail", "2"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["line-1", "line-2"]


def test_jobs_logs_grep_filters_lines(fake_aap: Any) -> None:
    fake_aap.seed(
        "jobs",
        id=42,
        status="successful",
        stdout="info: ok\nERROR: boom\ninfo: done\n",
    )
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "--grep", "ERROR"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ERROR: boom"


def test_jobs_logs_grep_ignore_case(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, status="successful", stdout="error: lower\nfine\n")
    result = CliRunner().invoke(
        app,
        ["jobs", "logs", "42", "--grep", "ERROR", "--ignore-case"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "error: lower"


def test_jobs_logs_invalid_grep_pattern_rejected_at_boundary(fake_aap: Any) -> None:
    """An unterminated character class is user input, not a bug — it must
    surface as a clean ``BadParameter`` (typer exit 2), not a Python
    traceback through ``report_errors`` (which only translates
    ``UntapedError``)."""
    fake_aap.seed("jobs", id=42, status="successful", stdout="anything\n")
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "--grep", "[unclosed"])
    assert result.exit_code != 0
    assert "is not a valid regex" in result.output
    # Make sure the underlying Python re.error didn't escape.
    assert "Traceback" not in result.output


def test_jobs_get_with_kind_workflow_job_hits_workflow_jobs_endpoint(fake_aap: Any) -> None:
    """``jobs get --kind workflow_job <id>`` routes to ``workflow_jobs/<id>/``,
    not the default ``jobs/<id>/`` (which would 404 for workflow_job ids).
    PollingJobMonitor and WatchJob already understand this kind via
    ``KIND_TO_API_PATH`` — wiring it through the CLI completes the path."""
    fake_aap.seed(
        "workflow_jobs",
        id=999,
        name="nightly-pipeline",
        status="successful",
    )
    result = CliRunner().invoke(
        app,
        ["jobs", "get", "999", "--kind", "workflow_job", "--format", "raw", "--columns", "name"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "nightly-pipeline"


def _seed_basic_jt(fake: Any, *, job_status: str) -> None:
    """Seed JT prerequisites for ``job-templates launch deploy --track``.

    FakeAap's ``_action`` handler materialises the launched job record at
    a fresh id (using ``next_action_status`` for its terminal status), so
    we don't need to pre-seed anything under the job id.
    """
    fake.seed("organizations", id=1, name="Default")
    fake.seed(
        "inventories", id=20, name="prod", organization=1, organization_name="Default", kind=""
    )
    fake.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    fake.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        project=10,
        project_name="playbooks",
        inventory=20,
        inventory_name="prod",
        playbook="deploy.yml",
    )
    fake.next_action_status = job_status


def test_launch_track_exits_zero_on_successful_job(fake_aap: Any) -> None:
    _seed_basic_jt(fake_aap, job_status="successful")
    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy", "--track"])
    assert result.exit_code == 0, result.output


def test_launch_track_exits_one_on_job_failure(fake_aap: Any) -> None:
    _seed_basic_jt(fake_aap, job_status="failed")
    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy", "--track"])
    assert result.exit_code == 1


def _seed_two_jts(fake: Any) -> None:
    """Seed shared FK prerequisites + two distinct JTs (``deploy-a``,
    ``deploy-b``) for the multi-template parallel-launch tests.
    """
    fake.seed("organizations", id=1, name="Default")
    fake.seed(
        "inventories", id=20, name="prod", organization=1, organization_name="Default", kind=""
    )
    fake.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    fake.seed(
        "job_templates",
        id=30,
        name="deploy-a",
        organization=1,
        organization_name="Default",
        project=10,
        project_name="playbooks",
        inventory=20,
        inventory_name="prod",
        playbook="a.yml",
    )
    fake.seed(
        "job_templates",
        id=31,
        name="deploy-b",
        organization=1,
        organization_name="Default",
        project=10,
        project_name="playbooks",
        inventory=20,
        inventory_name="prod",
        playbook="b.yml",
    )


def test_launch_track_parallel_drains_concurrently(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two ``--track`` jobs must drain concurrently. We prove it by
    blocking each worker on a 2-party :class:`threading.Barrier`: a
    sequential implementation can never reach the second worker, the
    barrier times out, and the test fails.
    """
    import threading

    from untaped_awx.cli import resource_commands

    _seed_two_jts(fake_aap)
    barrier = threading.Barrier(2, timeout=5)

    class _BarrierStream:
        def __init__(self, monitor: Any) -> None:
            pass

        def __call__(self, job: Any, *, follow: bool = True, **_kwargs: Any) -> Any:
            barrier.wait()
            return iter(())

    monkeypatch.setattr(resource_commands, "StreamJobEvents", _BarrierStream)

    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy-a", "deploy-b", "--track"])
    assert result.exit_code == 0, result.output


def test_launch_track_output_lines_carry_template_prefix(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent multi-template event output must be prefixed with the
    originating template name so a shared stderr stays disambiguable.
    """
    from untaped_awx.cli import resource_commands
    from untaped_awx.domain import JobEvent

    _seed_two_jts(fake_aap)

    class _StubStream:
        def __init__(self, monitor: Any) -> None:
            pass

        def __call__(self, job: Any, *, follow: bool = True, **_kwargs: Any) -> Any:
            # One identifiable event per worker; the play name carries
            # the materialised job id so test failure messages tell us
            # which worker emitted what.
            return iter([JobEvent(counter=1, event="playbook_on_play_start", play=f"job-{job.id}")])

    monkeypatch.setattr(resource_commands, "StreamJobEvents", _StubStream)

    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy-a", "deploy-b", "--track"])
    assert result.exit_code == 0, result.output
    assert "[deploy-a]" in result.stderr
    assert "[deploy-b]" in result.stderr


def test_launch_track_one_failed_exits_one_and_logs_both(fake_aap: Any) -> None:
    """Mixed terminal statuses across templates: one ``failed`` → exit 1.
    The ``next_action_status`` override is one-shot, so ``deploy-a``
    (first launch) ends ``failed`` and ``deploy-b`` defaults back to
    ``successful``.
    """
    _seed_two_jts(fake_aap)
    fake_aap.next_action_status = "failed"

    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy-a", "deploy-b", "--track"])
    assert result.exit_code == 1, result.output
