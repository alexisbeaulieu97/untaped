"""``job-templates copy`` / ``workflow-templates copy`` end to end."""

from __future__ import annotations

import json
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def _seed(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed("organizations", id=2, name="Other")
    fake.seed("projects", id=10, name="playbooks", organization=1)
    fake.seed("labels", id=50, name="web", organization=1)
    fake.seed(
        "job_templates",
        id=30,
        name="Deploy",
        organization=1,
        project=10,
        playbook="deploy.yml",
        scm_branch="",
    )
    fake.memberships[("job_templates", 30, "labels")] = {50}
    fake.seed("workflow_job_templates", id=60, name="Release train", organization=1)


def _copies(fake: Any) -> list[tuple[str, int, str, dict[str, Any]]]:
    return [call for call in fake.actions_called if call[2] == "copy"]


def _copy(*args: str) -> Any:
    return CliInvoker().invoke(app, ["job-templates", "copy", *args])


def test_copy_creates_the_new_template_and_reports_it(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = _copy(
        "Deploy", "--name", "Deploy next", "--organization", "Default", "--yes", "--format", "json"
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    outcome = json.loads(result.stdout)
    new = fake_aap.get_record("job_templates", outcome["id"])
    assert new["name"] == "Deploy next"
    assert outcome == {
        "id": new["id"],
        "name": "Deploy next",
        "source_id": 30,
        "kind": "JobTemplate",
        "action": "created",
        "not_carried": [],
        "detail": None,
    }
    assert "Copy JobTemplate/Deploy id=30" in result.stderr
    assert fake_aap.memberships[("job_templates", new["id"], "labels")] == {50}


def test_copy_refuses_a_name_taken_in_the_same_organization(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.seed("job_templates", id=31, name="Deploy next", organization=1)

    result = _copy("Deploy", "--name", "Deploy next", "--yes")

    assert result.exit_code == 1
    assert "'Deploy next' already exists" in result.stderr
    assert _copies(fake_aap) == []


def test_copy_allows_a_name_used_only_in_another_organization(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.seed("job_templates", id=31, name="Deploy next", organization=2)

    result = _copy("Deploy", "--name", "Deploy next", "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert len(_copies(fake_aap)) == 1


def test_copy_refuses_when_awx_reports_can_copy_false(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.copy_checks[("job_templates", 30)] = {"can_copy": False}

    result = _copy("Deploy", "--name", "Deploy next", "--yes")

    assert result.exit_code == 1
    assert "can_copy: false" in result.stderr
    assert _copies(fake_aap) == []


def test_dry_run_previews_without_writing(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = _copy("Deploy", "--name", "Deploy next", "--dry-run", "--format", "json")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert json.loads(result.stdout)["action"] == "planned"
    assert json.loads(result.stdout)["id"] is None
    assert _copies(fake_aap) == []
    assert not [c for c in fake_aap.router.calls if c.request.method != "GET"]


def test_copy_requires_yes_when_not_interactive(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = _copy("Deploy", "--name", "Deploy next")

    assert result.exit_code == 2
    assert "--yes" in result.stderr
    assert _copies(fake_aap) == []


def test_preview_names_what_awx_will_not_copy(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.copy_checks[("workflow_job_templates", 60)] = {
        "can_copy": True,
        "can_copy_without_user_input": False,
        "templates_unable_to_copy": ["Deploy"],
        "credentials_unable_to_copy": [],
        "inventories_unable_to_copy": ["Production"],
    }

    result = CliInvoker().invoke(
        app,
        [
            "workflow-templates",
            "copy",
            "Release train",
            "--name",
            "Release train 2",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert "warning: AWX will not copy templates: Deploy" in result.stderr
    assert "warning: AWX will not copy inventories: Production" in result.stderr
    assert json.loads(result.stdout)["not_carried"] == [
        "templates: Deploy",
        "inventories: Production",
    ]


def test_copy_pipes_into_patch_on_the_new_template(fake_aap: Any) -> None:
    _seed(fake_aap)
    copied = _copy("Deploy", "--name", "Deploy next", "--format", "pipe", "--yes")
    assert copied.exit_code == 0, copied.output + (copied.stderr or "")
    new_id = json.loads(copied.stdout)["record"]["id"]

    preview = CliInvoker().invoke(
        app,
        ["job-templates", "patch", "--stdin", "--set", "scm_branch=main", "--dry-run"],
        input=copied.stdout,
    )
    assert preview.exit_code == 0, preview.output + (preview.stderr or "")
    assert f"id={new_id}" in preview.stderr + preview.stdout

    patched = CliInvoker().invoke(
        app,
        ["job-templates", "patch", "--stdin", "--set", "scm_branch=main", "--yes"],
        input=copied.stdout,
    )
    assert patched.exit_code == 0, patched.output + (patched.stderr or "")
    assert fake_aap.get_record("job_templates", new_id)["scm_branch"] == "main"
    assert fake_aap.get_record("job_templates", 30)["scm_branch"] == ""


def test_copy_outcome_of_another_kind_is_refused_by_patch(fake_aap: Any) -> None:
    _seed(fake_aap)
    record = {"id": 60, "name": "x", "source_id": 1, "kind": "WorkflowJobTemplate"}
    envelope = json.dumps({"untaped": "1", "kind": "awx.copy_outcome", "record": record})

    result = CliInvoker().invoke(
        app,
        ["job-templates", "patch", "--stdin", "--set", "scm_branch=main", "--yes"],
        input=envelope + "\n",
    )

    assert result.exit_code != 0
    assert "expected 'JobTemplate'" in result.stderr


def test_copy_refuses_the_source_name(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = _copy("Deploy", "--name", "Deploy", "--yes")

    assert result.exit_code == 1
    assert "is the source" in result.stderr
    assert _copies(fake_aap) == []
