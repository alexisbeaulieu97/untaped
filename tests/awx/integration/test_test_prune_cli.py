"""``awx test prune``: delete the temporary copies a killed ``test run --source-ref`` left."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.capabilities.awx.domain.temporary_set import Marker
from untaped.testing import CliInvoker, ScriptedPromptBackend

if TYPE_CHECKING:  # pragma: no cover — pytest --import-mode=importlib hides 'tests'
    from awx.conftest import FakeAap
else:
    FakeAap = object  # type: ignore[assignment,misc]

pytestmark = pytest.mark.integration


def _copy(fake: FakeAap, path: str, name: str, run_id: str, age: timedelta) -> dict[str, Any]:
    marker = Marker(run_id=run_id, ref="feature/x", sha="1a2b3c4", created=datetime.now(UTC) - age)
    return fake.seed(
        path,
        name=f"{name} [untaped-test 1a2b3c4 {run_id}]",
        description=marker.render(),
        organization=1,
        summary_fields={"organization": {"id": 1, "name": "Default"}},
    )


@pytest.fixture
def aap(fake_aap: FakeAap) -> FakeAap:
    fake_aap.seed("organizations", id=1, name="Default")
    return fake_aap


def _prune(*args: str) -> Any:
    return CliInvoker().invoke(app, ["test", "prune", "-f", "json", *args])


def test_old_copies_are_deleted_workflows_first(aap: FakeAap) -> None:
    job_template = _copy(aap, "job_templates", "Deploy", "k3x9", timedelta(hours=3))
    workflow = _copy(aap, "workflow_job_templates", "Release", "k3x9", timedelta(hours=3))
    fresh = _copy(aap, "job_templates", "Deploy", "abcd", timedelta(minutes=5))
    # Named like a copy, but its description is someone's own: never touched.
    lookalike = aap.seed(
        "job_templates", name="Deploy [untaped-test 1a2b3c4 zzzz]", description="mine"
    )

    result = _prune("--yes")

    assert result.exit_code == 0, result.stderr
    rows = json.loads(result.stdout)
    assert [(row["kind"], row["id"], row["action"]) for row in rows] == [
        ("WorkflowJobTemplate", workflow["id"], "deleted"),
        ("JobTemplate", job_template["id"], "deleted"),
    ]
    assert rows[0] | {"created_at": None} == {
        "id": workflow["id"],
        "name": "Release [untaped-test 1a2b3c4 k3x9]",
        "kind": "WorkflowJobTemplate",
        "template": "Release",
        "organization": "Default",
        "run_id": "k3x9",
        "ref": "feature/x",
        "sha": "1a2b3c4",
        "created_at": None,
        "path": None,
        "prompts": [],
        "action": "deleted",
        "detail": None,
    }
    remaining = {record["id"] for record in aap.list_records("job_templates")}
    assert remaining == {fresh["id"], lookalike["id"]}
    assert aap.list_records("workflow_job_templates") == []


def test_run_prunes_only_that_runs_copies(aap: FakeAap) -> None:
    mine = _copy(aap, "job_templates", "Deploy", "k3x9", timedelta(0))
    other = _copy(aap, "job_templates", "Deploy", "abcd", timedelta(0))

    result = _prune("--run", "k3x9", "--older-than", "0", "--yes")

    assert result.exit_code == 0, result.stderr
    assert [row["id"] for row in json.loads(result.stdout)] == [mine["id"]]
    assert aap.list_records("job_templates") == [other]


def test_dry_run_lists_without_deleting(aap: FakeAap) -> None:
    copy = _copy(aap, "job_templates", "Deploy", "k3x9", timedelta(minutes=10))

    result = _prune("--older-than", "5m", "--dry-run")

    assert result.exit_code == 0, result.stderr
    assert [(row["id"], row["action"]) for row in json.loads(result.stdout)] == [
        (copy["id"], "planned")
    ]
    assert aap.list_records("job_templates") == [copy]


def test_older_than_zero_takes_every_copy(aap: FakeAap) -> None:
    _copy(aap, "job_templates", "Deploy", "k3x9", timedelta(0))

    result = _prune("--older-than", "0", "--yes")

    assert result.exit_code == 0, result.stderr
    assert aap.list_records("job_templates") == []


def test_a_copy_that_cannot_be_deleted_fails_its_row(aap: FakeAap) -> None:
    copy = _copy(aap, "job_templates", "Deploy", "k3x9", timedelta(hours=3))
    aap.delete_error = 403

    result = _prune("--yes")

    assert result.exit_code == 4
    [row] = json.loads(result.stdout)
    assert (row["id"], row["action"]) == (copy["id"], "failed")
    assert row["error"]["category"] == "permission"


def test_without_a_terminal_prune_needs_yes(aap: FakeAap) -> None:
    _copy(aap, "job_templates", "Deploy", "k3x9", timedelta(hours=3))

    result = _prune()

    assert result.exit_code == 2
    assert "--yes" in result.stderr
    assert "copys" not in result.stderr


def test_a_declined_prune_deletes_and_prints_nothing(aap: FakeAap) -> None:
    copy = _copy(aap, "job_templates", "Deploy", "k3x9", timedelta(hours=3))
    backend = ScriptedPromptBackend(confirms=[False])

    result = CliInvoker().invoke(
        app, ["test", "prune", "-f", "json"], interactive=True, prompt_backend=backend
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "About to delete 1 temporary template" in result.stderr
    assert result.stderr.rstrip().endswith("cancelled; no changes made")
    assert aap.list_records("job_templates") == [copy]


def test_nothing_to_prune(aap: FakeAap) -> None:
    result = CliInvoker().invoke(app, ["test", "prune", "--yes"])

    assert result.exit_code == 0, result.stderr
    assert "No temporary copies found." in result.stderr


def test_a_bad_age_is_a_usage_error(aap: FakeAap) -> None:
    result = _prune("--older-than", "2 hours")

    assert result.exit_code == 2
    assert "--older-than: '2 hours' is not an age like 2h" in result.stderr
