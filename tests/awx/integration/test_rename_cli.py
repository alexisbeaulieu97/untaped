"""``job-templates rename`` / ``workflow-templates rename`` end to end."""

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
    fake.seed("job_templates", id=30, name="Deploy", organization=1, scm_branch="")
    fake.seed("workflow_job_templates", id=60, name="Release train", organization=None)


def _writes(fake: Any) -> list[Any]:
    return [call for call in fake.router.calls if call.request.method != "GET"]


def _rename(*args: str) -> Any:
    return CliInvoker().invoke(app, ["job-templates", "rename", *args])


def test_rename_writes_only_the_name_and_reports_it(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = _rename(
        "Deploy", "Deploy app", "--organization", "Default", "--yes", "--format", "json"
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert json.loads(result.stdout) == {
        "id": 30,
        "name": "Deploy app",
        "old_name": "Deploy",
        "kind": "JobTemplate",
        "action": "renamed",
        "detail": None,
    }
    assert "Rename JobTemplate id=30 scope=org=Default: 'Deploy' -> 'Deploy app'" in (result.stderr)
    [patch] = _writes(fake_aap)
    assert json.loads(patch.request.content) == {"name": "Deploy app"}
    assert fake_aap.get_record("job_templates", 30)["name"] == "Deploy app"


def test_rename_refuses_a_name_taken_in_the_same_scope(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.seed("job_templates", id=31, name="Deploy app", organization=1)

    result = _rename("Deploy", "Deploy app", "--yes")

    assert result.exit_code == 1
    assert "'Deploy app' already exists in the same scope" in result.stderr
    assert _writes(fake_aap) == []


def test_rename_ignores_the_name_in_another_organization(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.seed("job_templates", id=31, name="Deploy app", organization=2)

    result = _rename("Deploy", "Deploy app", "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")


def test_rename_org_less_workflow_checks_org_less_scope(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.seed("workflow_job_templates", id=61, name="Nightly", organization=None)

    taken = CliInvoker().invoke(
        app, ["workflow-templates", "rename", "Release train", "Nightly", "--yes"]
    )
    assert taken.exit_code == 1
    assert "already exists" in taken.stderr

    free = CliInvoker().invoke(
        app, ["workflow-templates", "rename", "Release train", "Weekly", "--yes"]
    )
    assert free.exit_code == 0, free.output + (free.stderr or "")
    assert fake_aap.get_record("workflow_job_templates", 60)["name"] == "Weekly"


def test_dry_run_previews_old_and_new_names_without_writing(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = _rename("Deploy", "Deploy app", "--dry-run", "--format", "json")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert json.loads(result.stdout)["action"] == "planned"
    assert "'Deploy' -> 'Deploy app'" in result.stderr
    assert _writes(fake_aap) == []
    assert fake_aap.get_record("job_templates", 30)["name"] == "Deploy"


def test_rename_requires_yes_when_not_interactive(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = _rename("Deploy", "Deploy app")

    assert result.exit_code == 2
    assert _writes(fake_aap) == []


def test_rename_to_the_current_name_is_refused(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = _rename("Deploy", "Deploy", "--yes")

    assert result.exit_code == 1
    assert "already has that name" in result.stderr
    assert _writes(fake_aap) == []


def test_rename_fails_when_the_re_read_shows_another_name(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.ignored_write_fields = {"name"}

    result = _rename("Deploy", "Deploy app", "--yes", "--format", "json")

    assert result.exit_code == 1
    outcome = json.loads(result.stdout)
    assert outcome["action"] == "failed"
    assert outcome["detail"] == "re-read shows name 'Deploy', not 'Deploy app'"
    assert "error: JobTemplate#30: re-read shows name 'Deploy'" in result.stderr


def test_rename_pipes_into_patch_on_the_renamed_template(fake_aap: Any) -> None:
    _seed(fake_aap)
    renamed = _rename("Deploy", "Deploy app", "--format", "pipe", "--yes")
    assert renamed.exit_code == 0, renamed.output + (renamed.stderr or "")
    assert json.loads(renamed.stdout)["kind"] == "awx.rename_outcome"

    patched = CliInvoker().invoke(
        app,
        ["job-templates", "patch", "--stdin", "--set", "scm_branch=main", "--yes"],
        input=renamed.stdout,
    )

    assert patched.exit_code == 0, patched.output + (patched.stderr or "")
    assert fake_aap.get_record("job_templates", 30)["scm_branch"] == "main"


def test_patch_still_rejects_name(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = CliInvoker().invoke(
        app, ["job-templates", "patch", "Deploy", "--set", "name=Other", "--yes"]
    )

    assert result.exit_code != 0
    assert "patch cannot change identity fields: name" in result.stderr
