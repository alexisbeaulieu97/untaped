"""``awx jobs cancel`` / ``jobs relaunch`` and ``jobs list --template``."""

from __future__ import annotations

import json
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker, ScriptedPromptBackend

pytestmark = pytest.mark.integration


def _posts(fake: Any) -> list[str]:
    return [
        call.request.url.path.removeprefix("/api/v2/")
        for call in fake.router.calls
        if call.request.method == "POST"
    ]


def _seed_jobs(fake: Any) -> None:
    fake.seed("jobs", id=41, name="deploy", status="running")
    fake.seed("jobs", id=42, name="backup", status="pending")
    fake.seed("jobs", id=43, name="old", status="successful")


# ---- cancel ----


def test_cancel_confirms_then_requests_cancel(fake_aap: Any) -> None:
    _seed_jobs(fake_aap)
    backend = ScriptedPromptBackend(confirms=[True])

    result = CliInvoker().invoke(
        app,
        ["jobs", "cancel", "41", "42", "--format", "json"],
        interactive=True,
        prompt_backend=backend,
    )

    assert result.exit_code == 0, result.output
    assert backend.calls == [("confirm", "Cancel 2 jobs?")]
    assert _posts(fake_aap) == ["jobs/41/cancel/", "jobs/42/cancel/"]
    rows = json.loads(result.stdout)
    assert [(r["id"], r["kind"], r["status"], r["action"]) for r in rows] == [
        (41, "job", "running", "cancel_requested"),
        (42, "job", "pending", "cancel_requested"),
    ]
    assert "cancel job 41 'deploy' (running)" in result.stderr


def test_cancel_decline_writes_nothing(fake_aap: Any) -> None:
    _seed_jobs(fake_aap)

    result = CliInvoker().invoke(
        app,
        ["jobs", "cancel", "41"],
        interactive=True,
        prompt_backend=ScriptedPromptBackend(confirms=[False]),
    )

    assert result.exit_code == 1, result.output
    assert result.stderr.endswith("cancelled; no changes made\n")
    assert _posts(fake_aap) == []


def test_cancel_dry_run_previews_planned_rows(fake_aap: Any) -> None:
    _seed_jobs(fake_aap)

    result = CliInvoker().invoke(
        app, ["jobs", "cancel", "41", "--dry-run", "--yes", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["action"] == "planned"
    assert _posts(fake_aap) == []


def test_cancel_requires_yes_when_not_interactive(fake_aap: Any) -> None:
    _seed_jobs(fake_aap)

    result = CliInvoker().invoke(app, ["jobs", "cancel", "41"])

    assert result.exit_code == 2, result.output
    assert "--yes" in result.stderr
    assert _posts(fake_aap) == []


def test_cancel_skips_finished_jobs_without_a_prompt(fake_aap: Any) -> None:
    _seed_jobs(fake_aap)

    result = CliInvoker().invoke(app, ["jobs", "cancel", "43", "--format", "json"])

    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)[0]
    assert (row["action"], row["detail"]) == ("skipped", "already successful")
    assert _posts(fake_aap) == []


def test_cancel_missing_job_rejects_the_batch(fake_aap: Any) -> None:
    _seed_jobs(fake_aap)

    result = CliInvoker().invoke(app, ["jobs", "cancel", "41", "999", "--yes"])

    assert result.exit_code == 1, result.output
    assert "error: 999:" in result.stderr
    assert _posts(fake_aap) == []


def test_cancel_refused_by_controller_is_a_failed_row(fake_aap: Any) -> None:
    _seed_jobs(fake_aap)
    fake_aap.refuse_cancel_ids.add(41)

    result = CliInvoker().invoke(app, ["jobs", "cancel", "41", "42", "--yes", "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert [r["action"] for r in rows] == ["failed", "cancel_requested"]
    assert "HTTP 405" in rows[0]["detail"]


def test_cancel_uses_the_piped_execution_kind(fake_aap: Any) -> None:
    fake_aap.seed("workflow_jobs", id=7, name="pipeline", status="running")
    record = {"id": 7, "kind": "workflow_job", "action": "completed"}
    piped = json.dumps({"untaped": "1", "kind": "awx.launch_outcome", "record": record})

    result = CliInvoker().invoke(app, ["jobs", "cancel", "--stdin", "--yes"], input=piped + "\n")

    assert result.exit_code == 0, result.output
    assert _posts(fake_aap) == ["workflow_jobs/7/cancel/"]


# ---- relaunch ----


def test_relaunch_failed_hosts_posts_and_reports_the_new_job(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=41, name="deploy", status="failed")
    backend = ScriptedPromptBackend(confirms=[True])

    result = CliInvoker().invoke(
        app,
        ["jobs", "relaunch", "41", "--failed-hosts", "--format", "json"],
        interactive=True,
        prompt_backend=backend,
    )

    assert result.exit_code == 0, result.output
    assert backend.calls == [("confirm", "Relaunch 1 job?")]
    assert fake_aap.actions_called[-1][1:] == (41, "relaunch", {"hosts": "failed"})
    row = json.loads(result.stdout)[0]
    assert row["action"] == "relaunched"
    assert row["target_id"] == 41
    assert row["id"] != 41
    assert row["kind"] == "job"
    assert row["hosts"] == "failed"


def test_relaunch_dry_run_and_pipe_into_wait(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=41, name="deploy", status="failed")

    dry = CliInvoker().invoke(app, ["jobs", "relaunch", "41", "--dry-run", "--format", "json"])
    assert dry.exit_code == 0, dry.output
    assert json.loads(dry.stdout)[0]["action"] == "planned"
    assert _posts(fake_aap) == []

    launched = CliInvoker().invoke(app, ["jobs", "relaunch", "41", "--yes", "--format", "pipe"])
    assert launched.exit_code == 0, launched.output
    waited = CliInvoker().invoke(
        app, ["jobs", "wait", "--stdin", "--format", "json"], input=launched.stdout
    )
    assert waited.exit_code == 0, waited.output
    assert json.loads(waited.stdout)[0]["id"] != 41


def test_relaunch_failed_hosts_rejects_other_kinds(fake_aap: Any) -> None:
    fake_aap.seed("workflow_jobs", id=7, name="pipeline", status="failed")

    result = CliInvoker().invoke(
        app, ["jobs", "relaunch", "7", "--kind", "workflow_job", "--failed-hosts", "--yes"]
    )

    assert result.exit_code == 2, result.output
    assert "--failed-hosts" in result.stderr
    assert _posts(fake_aap) == []


def test_relaunch_rejects_kinds_without_a_relaunch_route(fake_aap: Any) -> None:
    fake_aap.seed("project_updates", id=8, name="sync", status="failed")

    result = CliInvoker().invoke(
        app, ["jobs", "relaunch", "8", "--kind", "project_update", "--yes"]
    )

    assert result.exit_code == 2, result.output
    assert "cannot be relaunched" in result.stderr
    assert _posts(fake_aap) == []


# ---- list --template ----


@pytest.mark.parametrize("template", ["deploy", "10"])
def test_jobs_list_template_filters_by_name_or_id(fake_aap: Any, template: str) -> None:
    fake_aap.seed("job_templates", id=10, name="deploy")
    fake_aap.seed("job_templates", id=11, name="backup")
    fake_aap.seed("jobs", id=41, name="deploy", status="successful", job_template=10)
    fake_aap.seed("jobs", id=42, name="backup", status="successful", job_template=11)

    result = CliInvoker().invoke(app, ["jobs", "list", "--template", template, "--format", "json"])

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.stdout)] == [41]


def test_jobs_list_template_uses_the_kind_template_field(fake_aap: Any) -> None:
    fake_aap.seed("projects", id=3, name="playbooks")
    fake_aap.seed("project_updates", id=51, name="playbooks", status="successful", project=3)
    fake_aap.seed("project_updates", id=52, name="other", status="successful", project=4)

    result = CliInvoker().invoke(
        app,
        ["jobs", "list", "--kind", "project_update", "--template", "3", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert [row["id"] for row in json.loads(result.stdout)] == [51]


def test_jobs_list_template_is_rejected_for_ad_hoc_commands(fake_aap: Any) -> None:
    result = CliInvoker().invoke(
        app, ["jobs", "list", "--kind", "ad_hoc_command", "--template", "x"]
    )

    assert result.exit_code == 2, result.output
    assert "--template" in result.stderr
