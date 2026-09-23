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


def test_job_template_credential_replacement_with_same_type_succeeds(
    fake_aap: Any, tmp_path: Path
) -> None:
    """AWX rejects two same-type credentials, so the old one must leave first."""
    _seed_basic(fake_aap)
    fake_aap.seed("credentials", id=40, name="ssh-old", organization=1, credential_type=1)
    fake_aap.seed("credentials", id=41, name="ssh-new", organization=1, credential_type=1)
    fake_aap.memberships[("job_templates", 30, "credentials")] = {40}
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec:\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
        "  credentials: [ssh-new]\n"
    )

    result = CliInvoker().invoke(app, ["job-templates", "apply", str(f), "--yes"])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert fake_aap.memberships[("job_templates", 30, "credentials")] == {41}


def _jt_credentials_doc(tmp_path: Path, *names: str) -> Path:
    f = tmp_path / "jt.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec:\n"
        "  playbook: deploy.yml\n"
        "  project: playbooks\n"
        "  inventory: prod\n"
        f"  credentials: [{', '.join(names)}]\n"
    )
    return f


def test_credential_replacement_associates_before_removing_other_types(
    fake_aap: Any, tmp_path: Path
) -> None:
    """A failed associate must not leave the template stripped of its credential."""
    _seed_basic(fake_aap)
    fake_aap.seed("credentials", id=40, name="ssh", organization=1, credential_type=1)
    fake_aap.seed("credentials", id=41, name="vault", organization=1, credential_type=3)
    fake_aap.memberships[("job_templates", 30, "credentials")] = {40}
    fake_aap.forbidden_associate_ids.add(41)

    result = CliInvoker().invoke(
        app, ["job-templates", "apply", str(_jt_credentials_doc(tmp_path, "vault")), "--yes"]
    )

    assert result.exit_code != 0, result.output
    assert fake_aap.memberships[("job_templates", 30, "credentials")] == {40}
    assert not any(
        json.loads(post.request.content or b"{}").get("disassociate") for post in _posts(fake_aap)
    )


def test_same_type_replacement_restores_the_old_credential_when_associate_fails(
    fake_aap: Any, tmp_path: Path
) -> None:
    _seed_basic(fake_aap)
    fake_aap.seed("credentials", id=40, name="ssh-old", organization=1, credential_type=1)
    fake_aap.seed("credentials", id=41, name="ssh-new", organization=1, credential_type=1)
    fake_aap.memberships[("job_templates", 30, "credentials")] = {40}
    fake_aap.forbidden_associate_ids.add(41)

    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "apply",
            str(_jt_credentials_doc(tmp_path, "ssh-new")),
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code != 0, result.output
    assert fake_aap.memberships[("job_templates", 30, "credentials")] == {40}
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "partial"
    assert "restored" in rows[0]["detail"]


def test_group_host_replacement_associates_first(fake_aap: Any, tmp_path: Path) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("inventories", id=20, name="prod", organization=1, organization_name="Default")
    fake_aap.seed("hosts", id=7, name="web-01", inventory=20)
    fake_aap.seed("hosts", id=8, name="web-02", inventory=20)
    fake_aap.seed(
        "groups",
        id=50,
        name="web",
        inventory=20,
        summary_fields={"inventory": {"id": 20, "name": "prod", "organization_name": "Default"}},
    )
    fake_aap.memberships[("groups", 50, "hosts")] = {7}
    fake_aap.forbidden_associate_ids.add(8)
    f = tmp_path / "g.yml"
    f.write_text(
        "kind: Group\n"
        "metadata:\n  name: web\n"
        "  parent: { kind: Inventory, name: prod, organization: Default }\n"
        "spec: { hosts: [web-02] }\n"
    )

    result = CliInvoker().invoke(app, ["groups", "apply", str(f), "--yes"])

    assert result.exit_code != 0, result.output
    assert fake_aap.memberships[("groups", 50, "hosts")] == {7}


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


def _two_orgs_with_project(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed("organizations", id=2, name="Other")
    fake.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=2,
        organization_name="Other",
        scm_type="git",
        description="other-org",
    )


@pytest.mark.parametrize(
    ("content", "command"),
    [
        (b"\xff\xfe not utf-8", ["apply"]),
        (b"kind: [\n", ["apply"]),
        (b"kind: Nope\nmetadata: { name: x }\n", ["apply"]),
        (
            b"kind: Project\nmetadata: { name: p, organization: Default }\n",
            ["job-templates", "apply"],
        ),
    ],
)
def test_directory_apply_errors_name_the_offending_file(
    fake_aap: Any, tmp_path: Path, content: bytes, command: list[str]
) -> None:
    _seed_basic(fake_aap)
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "good.yml").write_text(
        "kind: JobTemplate\nmetadata: { name: deploy, organization: Default }\n"
        "spec: { playbook: deploy.yml, project: playbooks, inventory: prod }\n"
    )
    (specs / "stray.yaml").write_bytes(content)

    result = CliInvoker().invoke(app, [*command, str(specs), "--dry-run"])

    assert result.exit_code != 0, result.output
    assert isinstance(result.exception, SystemExit)
    assert "stray.yaml" in result.stderr


def _set_default_organization(aap_config: Path) -> None:
    config = aap_config.read_text()
    prefix = "api_prefix: /api/v2/"
    indent = config.split(prefix)[0].rsplit("\n", 1)[1]
    aap_config.write_text(
        config.replace(prefix, f"{prefix}\n{indent}default_organization: Default")
    )


def test_apply_spec_organization_wins_over_default_organization(
    fake_aap: Any, aap_config: Path, tmp_path: Path
) -> None:
    """``spec.organization`` names the identity; the default must not fill it."""
    _set_default_organization(aap_config)
    _two_orgs_with_project(fake_aap)
    doc = tmp_path / "p.yml"
    doc.write_text(
        "kind: Project\nmetadata: { name: playbooks }\n"
        "spec: { scm_type: git, description: mine, organization: Other }\n"
    )

    result = CliInvoker().invoke(app, ["projects", "apply", str(doc), "--yes"])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert [(p["id"], p["description"]) for p in fake_aap.list_records("projects")] == [
        (10, "mine")
    ]


def test_save_apply_round_trip_keeps_org_less_workflow(
    fake_aap: Any, aap_config: Path, tmp_path: Path
) -> None:
    """An org-less record saves an explicit null identity that apply honours."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "workflow_job_templates", id=40, name="global-wf", organization=None, description="old"
    )
    out = tmp_path / "w.yml"
    saved = CliInvoker().invoke(
        app, ["workflow-templates", "export", "global-wf", "--out", str(out)]
    )
    assert saved.exit_code == 0, saved.output + (saved.stderr or "")
    assert "organization: null" in out.read_text()
    fake_aap.seed(
        "workflow_job_templates", id=41, name="global-wf", organization=1, description="scoped"
    )
    out.write_text(out.read_text().replace("description: old", "description: new"))
    _set_default_organization(aap_config)

    result = CliInvoker().invoke(app, ["workflow-templates", "apply", str(out), "--yes"])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    records = {r["id"]: r for r in fake_aap.list_records("workflow_job_templates")}
    assert sorted(records) == [40, 41]
    assert records[40]["description"] == "new"
    assert records[41]["description"] == "scoped"


def test_apply_without_org_uses_default_organization(
    fake_aap: Any, aap_config: Path, tmp_path: Path
) -> None:
    """A same-named resource in another org must not be updated."""
    config = aap_config.read_text()
    prefix = "api_prefix: /api/v2/"
    indent = config.split(prefix)[0].rsplit("\n", 1)[1]
    aap_config.write_text(
        config.replace(prefix, f"{prefix}\n{indent}default_organization: Default")
    )
    _two_orgs_with_project(fake_aap)
    doc = tmp_path / "p.yml"
    doc.write_text(
        "kind: Project\nmetadata: { name: playbooks }\nspec: { scm_type: git, description: mine }\n"
    )

    result = CliInvoker().invoke(app, ["projects", "apply", str(doc), "--yes"])

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert fake_aap.get_record("projects", 10)["description"] == "other-org"
    created = [p for p in fake_aap.list_records("projects") if p["id"] != 10]
    assert [(p["name"], p["organization"]) for p in created] == [("playbooks", 1)]
