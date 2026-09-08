"""End-to-end CLI tests for AWX resource apply flows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def _seed_basic(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default", description="")
    fake.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    fake.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
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
        description="deploy the app",
        last_job_status="successful",
        webhook_key="$encrypted$",
    )


def _patches(fake: Any) -> list[Any]:
    return [call for call in fake.router.calls if call.request.method == "PATCH"]


def _posts(fake: Any) -> list[Any]:
    return [call for call in fake.router.calls if call.request.method == "POST"]


def test_apply_preview_does_not_write(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec:\n"
        "  description: changed-via-apply\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
    )
    result = CliInvoker().invoke(app, ["job-templates", "apply", "--dry-run", str(f)])
    assert result.exit_code == 0, result.output
    # State on the server is unchanged because we didn't pass --yes.
    jt = fake_aap.get_record("job_templates", 30)
    assert jt["description"] == "deploy the app"


def test_apply_under_scoped_file_raises_ambiguity(fake_aap: Any, tmp_path: Path) -> None:
    """An under-scoped apply against ambiguous AWX state must surface the
    ambiguity rather than overwrite an arbitrary record."""
    fake_aap.seed("organizations", id=1, name="Org-A")
    fake_aap.seed("organizations", id=2, name="Org-B")
    fake_aap.seed("projects", id=10, name="playbooks", organization=1, organization_name="Org-A")
    fake_aap.seed("inventories", id=20, name="prod", organization=1, organization_name="Org-A")
    fake_aap.seed("job_templates", id=30, name="deploy", organization=1, organization_name="Org-A")
    fake_aap.seed("job_templates", id=31, name="deploy", organization=2, organization_name="Org-B")

    f = tmp_path / "jt.yml"
    # Note: no organization in metadata — that's the under-scoped case.
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy }\n"
        "spec:\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
    )
    result = CliInvoker().invoke(app, ["job-templates", "apply", str(f), "--yes"])
    output = result.output + (result.stderr or "")
    assert result.exit_code != 0, output
    assert "ambiguous" in output.lower(), output


def test_apply_yes_writes_changes(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec:\n"
        "  description: changed-via-apply\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
    )
    result = CliInvoker().invoke(app, ["job-templates", "apply", str(f), "--yes"])
    assert result.exit_code == 0, result.output
    jt = fake_aap.get_record("job_templates", 30)
    assert jt["description"] == "changed-via-apply"


def test_apply_ignored_passthrough_field_fails_by_default(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    fake_aap.ignored_write_fields.add("zzz_bogus")
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec:\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
        "  zzz_bogus: 1\n"
    )

    result = CliInvoker().invoke(app, ["job-templates", "apply", str(f), "--yes"])

    output = result.output + (result.stderr or "")
    assert result.exit_code == 1, output
    assert "zzz_bogus" in output
    assert "unverified" in output
    assert "zzz_bogus" not in fake_aap.get_record("job_templates", 30)


def test_apply_ignored_passthrough_field_can_be_allowed(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    fake_aap.ignored_write_fields.add("zzz_bogus")
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec:\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
        "  zzz_bogus: 1\n"
    )

    result = CliInvoker().invoke(
        app,
        ["job-templates", "apply", str(f), "--yes", "--allow-unverified"],
    )

    output = result.output + (result.stderr or "")
    assert result.exit_code == 0, output
    assert "updated" in result.stdout
    assert "zzz_bogus" in output
    assert "unverified" in output
    assert "zzz_bogus" not in fake_aap.get_record("job_templates", 30)


def test_apply_allow_unverified_requires_yes(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec: { playbook: deploy.yml, project: playbooks, inventory: prod }\n"
    )

    result = CliInvoker().invoke(app, ["apply", str(f), "--allow-unverified"])

    assert result.exit_code == 1
    assert "--yes" in (result.output + (result.stderr or ""))


def test_apply_real_secret_masking_and_survey_enrichment_do_not_false_fail(
    fake_aap: Any, tmp_path: Path
) -> None:
    _seed_basic(fake_aap)
    fake_aap.mask_secret_write_response = True
    fake_aap.enrich_survey_spec_response = True
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec:\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
        "  webhook_key: actual-secret\n"
        "  survey_spec:\n"
        "    name: deploy survey\n"
        "    spec:\n"
        "      - variable: password\n"
        "        question_name: Password\n"
        "        default: actual-secret\n"
    )

    result = CliInvoker().invoke(app, ["job-templates", "apply", str(f), "--yes"])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    jt = fake_aap.get_record("job_templates", 30)
    assert jt["webhook_key"] == "$encrypted$"
    assert jt["survey_spec"]["spec"][0]["required"] is False


def test_job_template_credentials_apply_reconciles_membership_not_body(
    fake_aap: Any, tmp_path: Path
) -> None:
    _seed_basic(fake_aap)
    fake_aap.seed("credentials", id=40, name="ssh", organization=1, organization_name="Default")
    fake_aap.seed("credentials", id=41, name="vault", organization=1, organization_name="Default")
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec:\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
        "  credentials: [ssh, vault]\n"
    )

    result = CliInvoker().invoke(app, ["job-templates", "apply", str(f), "--yes"])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    for patch in _patches(fake_aap):
        assert "credentials" not in json.loads(patch.request.content)
    assert fake_aap.memberships[("job_templates", 30, "credentials")] == {40, 41}
    assert any(
        "/job_templates/30/credentials/" in str(post.request.url) for post in _posts(fake_aap)
    )


def test_job_templates_credentials_add_remove_command_scopes_members_by_org(
    fake_aap: Any,
) -> None:
    _seed_basic(fake_aap)
    fake_aap.seed("organizations", id=2, name="Other")
    fake_aap.seed("credentials", id=40, name="ssh", organization=1, organization_name="Default")
    fake_aap.seed("credentials", id=50, name="ssh", organization=2, organization_name="Other")

    add = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "credentials",
            "add",
            "--yes",
            "deploy",
            "ssh",
            "--organization",
            "Default",
        ],
    )
    assert add.exit_code == 0, add.output + (add.stderr or "")
    assert fake_aap.memberships[("job_templates", 30, "credentials")] == {40}

    remove = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "credentials",
            "remove",
            "--yes",
            "deploy",
            "ssh",
            "--organization",
            "Default",
        ],
    )
    assert remove.exit_code == 0, remove.output + (remove.stderr or "")
    assert fake_aap.memberships[("job_templates", 30, "credentials")] == set()


def test_per_resource_apply_rejects_wrong_kind_before_writing(
    fake_aap: Any, tmp_path: Path
) -> None:
    """A `job-templates apply` must NOT write Project docs that share the file."""
    _seed_basic(fake_aap)
    original_project = dict(fake_aap.get_record("projects", 10))
    f = tmp_path / "mixed.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec: { playbook: changed.yml, project: playbooks, inventory: prod }\n"
        "---\n"
        "kind: Project\n"
        "metadata: { name: playbooks, organization: Default }\n"
        "spec: { scm_type: hg, scm_url: 'https://elsewhere/x.git' }\n"
    )
    result = CliInvoker().invoke(app, ["job-templates", "apply", str(f), "--yes"])
    assert result.exit_code != 0, result.output
    # The entire batch is rejected.
    jt = fake_aap.get_record("job_templates", 30)
    assert jt["playbook"] == "deploy.yml"
    # Project untouched — no scm_type=hg leaked through
    project = fake_aap.get_record("projects", 10)
    assert project["scm_type"] == original_project["scm_type"] == "git"
    # Wrong-kind error visible
    assert "Project" in result.stderr


def test_apply_creates_when_missing(seeded_default_org: Any, tmp_path: Path) -> None:
    f = tmp_path / "p.yml"
    f.write_text(
        "kind: Project\n"
        "metadata: { name: new-proj, organization: Default }\n"
        "spec:\n"
        "  scm_type: git\n"
        "  scm_url: https://example.com/x.git\n"
    )
    result = CliInvoker().invoke(app, ["projects", "apply", str(f), "--yes"])
    assert result.exit_code == 0, result.output
    new_proj = next(
        r for r in seeded_default_org.list_records("projects") if r["name"] == "new-proj"
    )
    assert new_proj["scm_type"] == "git"
    assert new_proj["organization"] == 1


def test_apply_preserves_encrypted_secret(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec:\n"
        "  description: still-deploy\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
        "  webhook_key: $encrypted$\n"
    )
    result = CliInvoker().invoke(app, ["job-templates", "apply", str(f), "--yes"])
    assert result.exit_code == 0, result.output
    jt = fake_aap.get_record("job_templates", 30)
    assert jt["webhook_key"] == "$encrypted$"  # untouched
    assert jt["description"] == "still-deploy"
