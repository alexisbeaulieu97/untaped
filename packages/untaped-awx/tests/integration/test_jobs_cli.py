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
        host="web-01",
        task="install",
    )
    fake.seed(
        "job_events",
        id=4,
        job=job_id,
        counter=4,
        event="runner_on_failed",
        host="api-01",
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
            "host",
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
