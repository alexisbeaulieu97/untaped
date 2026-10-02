"""The default ``table`` columns of AWX commands, for a human scanning rows.

Only ``--format table`` narrows a row to its defaults; json keeps the whole
record, and ``--columns +name`` / ``--columns=-name`` edit the defaults.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from untaped.testing import CliInvoker
from untaped_awx.cli import app

pytestmark = pytest.mark.integration


def _header(out: str) -> list[str]:
    return [cell.strip() for cell in out.splitlines()[1].strip("│").split("│")]


def _run(*args: str, input: str | None = None) -> Any:
    result = CliInvoker().invoke(app, list(args), input=input)
    assert result.exit_code == 0, result.output
    return result


_JOB = {
    "name": "deploy",
    "type": "job",
    "status": "failed",
    "launch_type": "manual",
    "started": "2026-01-01T00:00:00Z",
    "finished": "2026-01-01T00:01:42Z",
    "elapsed": 102.0,
    "job_explanation": "Previous Task Failed",
    "job_args": "[]",
    "failed": True,
    "scm_branch": "",
    "scm_revision": "",
    "summary_fields": {"inventory": {"id": 20, "name": "prod"}},
}


# ---- jobs ----


def test_jobs_list_table_shows_when_and_how_long(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, **_JOB)

    table = _run("jobs", "list").stdout
    records = json.loads(_run("jobs", "list", "--format", "json").stdout)

    assert _header(table) == ["id", "name", "status", "launch_type", "started", "elapsed"]
    assert records[0]["job_args"] == "[]"


def test_jobs_list_raw_keeps_its_projection(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, **_JOB)

    assert _run("jobs", "list", "--format", "raw").stdout.strip() == "42\tdeploy\tfailed"


def test_jobs_list_columns_edit_the_table_defaults(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, **_JOB)

    table = _run("jobs", "list", "--columns", "+finished", "--columns=-launch_type").stdout

    assert _header(table) == ["id", "name", "status", "started", "elapsed", "finished"]


def test_jobs_get_table_is_a_summary_not_every_awx_field(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, **_JOB)

    table = _run("jobs", "get", "42").stdout
    record = json.loads(_run("jobs", "get", "42", "--format", "json").stdout)[0]

    assert _header(table) == [
        "id",
        "name",
        "status",
        "started",
        "finished",
        "elapsed",
        "job_explanation",
    ]
    assert record["summary_fields"] == _JOB["summary_fields"]


def test_jobs_wait_table_shows_when_it_finished_and_how_long(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, **{**_JOB, "status": "successful", "failed": False})

    table = _run("jobs", "wait", "42").stdout
    record = json.loads(_run("jobs", "wait", "42", "--format", "json").stdout)[0]

    assert _header(table) == ["id", "name", "status", "finished_at", "elapsed"]
    assert "2026-01-01T00:01:42Z" in table
    assert "1m42s" in table
    assert record["elapsed"] == 102.0
    assert {"kind", "failed", "scm_branch"} <= record.keys()


def _seed_events(fake: Any) -> None:
    fake.seed("jobs", id=42, **{**_JOB, "status": "successful"})
    fake.seed(
        "job_events",
        id=1,
        job=42,
        counter=1,
        event="runner_on_ok",
        host_name="web-01",
        task="install",
        changed=True,
        stdout="changed: [web-01]",
    )


def test_jobs_events_table_shows_changed_and_failed(fake_aap: Any) -> None:
    _seed_events(fake_aap)

    table = _run("jobs", "events", "42").stdout
    rows = json.loads(_run("jobs", "events", "42", "--format", "json").stdout)

    assert _header(table) == ["counter", "event", "host_name", "task", "changed", "failed"]
    assert rows[0]["stdout"] == "changed: [web-01]"


def test_jobs_events_follow_json_applies_column_edits(fake_aap: Any) -> None:
    _seed_events(fake_aap)

    out = _run("jobs", "events", "42", "--follow", "--format", "json", "--columns=-stdout").stdout
    row = json.loads(out.splitlines()[0])

    assert "stdout" not in row
    assert row["counter"] == 1
    assert not any(key.startswith(("+", "-")) for key in row)


def test_jobs_logs_follow_json_applies_column_edits(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, **{**_JOB, "status": "successful"}, stdout="line-0\n")

    out = _run("jobs", "logs", "42", "--follow", "--format", "json", "--columns=-job").stdout

    assert json.loads(out.splitlines()[0]) == {"line": "line-0"}


def test_jobs_cancel_table_leaves_out_kind_and_the_status_read_before(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=41, name="deploy", status="running")

    table = _run("jobs", "cancel", "41", "--yes").stdout

    assert _header(table) == ["id", "name", "action"]


def test_jobs_relaunch_table_leaves_out_kind_and_hosts(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=41, name="deploy", status="failed")

    table = _run("jobs", "relaunch", "41", "--yes").stdout

    assert _header(table) == ["id", "name", "status", "target_id", "action"]


# ---- launch, sync, delete, patch, apply, membership ----


def test_launch_table_shows_the_target_and_its_execution(seeded_default_org: Any) -> None:
    seeded_default_org.seed("job_templates", id=10, name="alpha", organization=1)

    table = _run("job-templates", "launch", "alpha").stdout
    row = json.loads(_run("job-templates", "launch", "alpha", "--format", "json").stdout)[0]

    assert _header(table) == ["target_name", "id", "status", "action"]
    assert {"kind", "target_kind", "scope", "failed"} <= row.keys()


def test_delete_table_leaves_out_kind_and_scope(seeded_default_org: Any) -> None:
    seeded_default_org.seed("job_templates", id=10, name="alpha", organization=1)

    table = _run("job-templates", "delete", "alpha", "--dry-run").stdout

    assert _header(table) == ["id", "name", "action"]


def test_patch_table_names_what_changed_without_the_kind(seeded_default_org: Any) -> None:
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )

    table = _run("job-templates", "patch", "alpha", "--set", "description=new", "--yes").stdout

    assert _header(table) == ["id", "name", "action", "fields_changed"]


def test_apply_table_keeps_the_kind_of_a_mixed_batch(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    f = tmp_path / "project.yml"
    f.write_text(
        "kind: Project\n"
        "metadata: { name: playbooks, organization: Default }\n"
        "spec: { description: new, scm_type: git }\n"
    )

    table = _run("apply", str(f), "--dry-run").stdout

    assert _header(table) == ["id", "name", "kind", "action", "fields_changed"]


# ---- spec-driven list ----


def _seed_inventory(fake: Any) -> None:
    fake.seed("inventories", id=20, name="prod", organization=1, organization_name="Default")
    summary = {"inventory": {"id": 20, "name": "prod"}}
    fake.seed("hosts", id=101, name="web-01", inventory=20, enabled=True, summary_fields=summary)
    fake.seed("groups", id=201, name="web", inventory=20, summary_fields=summary)
    fake.seed(
        "inventory_sources",
        id=301,
        name="cloud",
        inventory=20,
        source="ec2",
        status="successful",
        summary_fields=summary,
    )


def test_list_table_names_foreign_keys_from_summary_fields(seeded_default_org: Any) -> None:
    _seed_inventory(seeded_default_org)

    table = _run("hosts", "list").stdout
    record = json.loads(_run("hosts", "list", "--format", "json").stdout)[0]

    assert _header(table) == ["id", "name", "inventory", "enabled"]
    assert "prod" in table.splitlines()[3]
    assert record["inventory"] == 20


@pytest.mark.parametrize(
    ("cli_name", "columns"),
    [
        ("groups", ["id", "name", "inventory"]),
        ("inventory-sources", ["id", "name", "source", "status", "inventory"]),
    ],
)
def test_list_table_scopes_inventory_children(
    seeded_default_org: Any, cli_name: str, columns: list[str]
) -> None:
    _seed_inventory(seeded_default_org)

    table = _run(cli_name, "list").stdout

    assert _header(table) == columns
    assert "prod" in table


def test_schedules_list_names_the_template_instead_of_a_last_run(
    seeded_default_org: Any,
) -> None:
    seeded_default_org.seed(
        "schedules",
        id=60,
        name="nightly",
        unified_job_template=10,
        next_run="2026-01-02T00:00:00Z",
        enabled=True,
        summary_fields={"unified_job_template": {"id": 10, "name": "deploy"}},
    )

    table = _run("schedules", "list").stdout

    assert _header(table) == ["id", "name", "unified_job_template", "next_run", "enabled"]
    assert "deploy" in table


def test_get_table_columns_can_be_edited(seeded_default_org: Any) -> None:
    _seed_inventory(seeded_default_org)

    table = _run("hosts", "get", "web-01", "--columns=-enabled").stdout

    assert _header(table) == ["id", "name", "inventory"]


# ---- usage, nodes ----


def test_usage_says_when_no_workflow_uses_the_template(seeded_default_org: Any) -> None:
    seeded_default_org.seed("job_templates", id=10, name="alpha", organization=1)

    result = _run("job-templates", "usage", "alpha")

    assert "No containing workflows found." in result.stderr


def test_nodes_says_when_a_workflow_is_empty(seeded_default_org: Any) -> None:
    seeded_default_org.seed("workflow_job_templates", id=10, name="wf", organization=1)

    result = _run("workflow-templates", "nodes", "wf")

    assert "No workflow nodes found." in result.stderr


def test_unified_templates_list_json_keeps_whole_records(fake_aap: Any) -> None:
    fake_aap.seed(
        "unified_job_templates", id=10, name="deploy", type="job_template", last_job_status="ok"
    )

    records = json.loads(_run("unified-templates", "list", "--format", "json").stdout)

    assert records[0]["last_job_status"] == "ok"


def test_jobs_list_raw_column_edits_start_from_the_table_defaults(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, **_JOB)

    out = _run("jobs", "list", "--format", "raw", "--columns", "+finished").stdout

    assert out.strip().split("\t") == [
        "42",
        "deploy",
        "failed",
        "manual",
        "2026-01-01T00:00:00Z",
        "102.0",
        "2026-01-01T00:01:42Z",
    ]


def test_jobs_events_raw_column_edits_start_from_the_table_defaults(fake_aap: Any) -> None:
    _seed_events(fake_aap)

    out = _run("jobs", "events", "42", "--format", "raw", "--columns=-task").stdout

    assert out.strip().split("\t") == ["1", "runner_on_ok", "web-01", "True", "False"]


def test_jobs_events_follow_json_lists_columns_once_and_streams_nothing(fake_aap: Any) -> None:
    _seed_events(fake_aap)
    fake_aap.seed("job_events", id=2, job=42, counter=2, event="runner_on_ok")

    result = _run("jobs", "events", "42", "--follow", "--format", "json", "--columns", "?")

    assert result.stdout == ""
    assert result.stderr.count("available columns") == 1


def test_jobs_events_follow_json_warns_once_about_an_unknown_column(fake_aap: Any) -> None:
    _seed_events(fake_aap)
    fake_aap.seed("job_events", id=2, job=42, counter=2, event="runner_on_ok")

    result = _run("jobs", "events", "42", "--follow", "--format", "json", "-c", "counter,nope")

    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        {"counter": 1, "nope": None},
        {"counter": 2, "nope": None},
    ]
    assert result.stderr.count("unknown column 'nope'") == 1
