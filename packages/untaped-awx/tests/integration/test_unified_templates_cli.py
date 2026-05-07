"""End-to-end CLI tests for ``untaped awx unified-templates``.

The Unified Job Templates endpoint is a polymorphic, read-only virtual
collection — AWX aggregates JT/WJT/Project/InventorySource rows behind
a single ``type`` discriminator. ``FakeAap`` is keyed by ``api_path``,
so we seed under ``"unified_job_templates"`` directly; the generic
``_list`` / ``_get`` routes already serve those endpoints without any
fixture changes.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner
from untaped_awx import app

pytestmark = pytest.mark.integration


def _seed_all_kinds(fake: Any) -> None:
    """Seed one row per UJT kind so the discriminator surface is exercised."""
    fake.seed(
        "unified_job_templates",
        id=10,
        name="deploy-app",
        type="job_template",
        last_job_status="successful",
        last_job_run="2026-05-01T00:00:00Z",
        summary_fields={"organization": {"id": 1, "name": "Default"}},
    )
    fake.seed(
        "unified_job_templates",
        id=20,
        name="weekly-rollup",
        type="workflow_job_template",
        last_job_status="failed",
        last_job_run="2026-05-02T00:00:00Z",
        summary_fields={"organization": {"id": 1, "name": "Default"}},
    )
    fake.seed(
        "unified_job_templates",
        id=30,
        name="playbooks-repo",
        type="project",
        status="successful",
        last_job_run="2026-05-03T00:00:00Z",
        summary_fields={"organization": {"id": 1, "name": "Default"}},
    )
    fake.seed(
        "unified_job_templates",
        id=40,
        name="cmdb-import",
        type="inventory_source",
        status="successful",
        last_job_run="2026-05-04T00:00:00Z",
        summary_fields={"organization": {"id": 1, "name": "Default"}},
    )


def test_list_returns_all_kinds(fake_aap: Any) -> None:
    _seed_all_kinds(fake_aap)
    result = CliRunner().invoke(
        app,
        ["unified-templates", "list", "--format", "raw", "--columns", "id"],
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines(), key=int)
    assert ids == ["10", "20", "30", "40"]


def test_list_default_columns_include_type_discriminator(fake_aap: Any) -> None:
    _seed_all_kinds(fake_aap)
    result = CliRunner().invoke(
        app,
        ["unified-templates", "list", "--format", "raw", "--columns", "id", "--columns", "type"],
    )
    assert result.exit_code == 0, result.output
    rows = sorted(result.stdout.strip().splitlines())
    assert rows == [
        "10\tjob_template",
        "20\tworkflow_job_template",
        "30\tproject",
        "40\tinventory_source",
    ]


def test_list_type_filter_narrows_to_one_kind(fake_aap: Any) -> None:
    _seed_all_kinds(fake_aap)
    result = CliRunner().invoke(
        app,
        ["unified-templates", "list", "--type", "project", "--format", "raw", "--columns", "name"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "playbooks-repo"


def test_list_type_collision_with_filter_fails_fast(fake_aap: Any) -> None:
    """``--type X --filter type=Y`` would compete on the same query
    param — refusing keeps precedence deterministic."""
    _seed_all_kinds(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "unified-templates",
            "list",
            "--type",
            "project",
            "--filter",
            "type=job_template",
        ],
    )
    assert result.exit_code != 0
    assert "--type" in result.output


def test_list_filter_passes_through_verbatim(fake_aap: Any) -> None:
    _seed_all_kinds(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "unified-templates",
            "list",
            "--filter",
            "name__icontains=rollup",
            "--format",
            "raw",
            "--columns",
            "name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "weekly-rollup"


def test_list_limit_caps_results(fake_aap: Any) -> None:
    _seed_all_kinds(fake_aap)
    result = CliRunner().invoke(
        app,
        ["unified-templates", "list", "--limit", "2", "--format", "raw", "--columns", "id"],
    )
    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 2


def test_get_multi_id_returns_full_records(fake_aap: Any) -> None:
    _seed_all_kinds(fake_aap)
    result = CliRunner().invoke(
        app,
        ["unified-templates", "get", "10", "30", "--format", "raw", "--columns", "name"],
    )
    assert result.exit_code == 0, result.output
    names = sorted(result.stdout.strip().splitlines())
    assert names == ["deploy-app", "playbooks-repo"]


def test_get_rejects_non_decimal_identifier(fake_aap: Any) -> None:
    _seed_all_kinds(fake_aap)
    result = CliRunner().invoke(app, ["unified-templates", "get", "deploy-app"])
    assert result.exit_code != 0
    assert "id-only" in result.output


def test_get_reports_missing_id_and_exits_nonzero(fake_aap: Any) -> None:
    """AWX has no ``/unified_job_templates/<id>/`` resource URL — we filter
    against the collection endpoint, so a missing id surfaces as an empty
    ``results`` list rather than a 404. The CLI must still emit a per-id
    stderr error and exit non-zero, while printing whichever ids did
    resolve.
    """
    _seed_all_kinds(fake_aap)  # ids 10, 20, 30, 40
    result = CliRunner().invoke(
        app,
        ["unified-templates", "get", "10", "999", "--format", "raw", "--columns", "id"],
    )
    assert result.exit_code == 1
    assert "999" in result.stderr
    assert "not found" in result.stderr
    # The found id (10) still prints to stdout.
    assert result.stdout.strip() == "10"


def test_get_stdin_round_trips_from_list_output(fake_aap: Any) -> None:
    """End-to-end pipe shape: ``list -f raw -c id | get --stdin``."""
    _seed_all_kinds(fake_aap)
    list_result = CliRunner().invoke(
        app,
        ["unified-templates", "list", "--format", "raw", "--columns", "id"],
    )
    assert list_result.exit_code == 0, list_result.output
    get_result = CliRunner().invoke(
        app,
        [
            "unified-templates",
            "get",
            "--stdin",
            "--format",
            "raw",
            "--columns",
            "name",
        ],
        input=list_result.stdout,
    )
    assert get_result.exit_code == 0, get_result.output
    names = sorted(get_result.stdout.strip().splitlines())
    assert names == ["cmdb-import", "deploy-app", "playbooks-repo", "weekly-rollup"]
