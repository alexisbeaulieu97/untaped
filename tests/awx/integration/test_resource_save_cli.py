"""End-to-end CLI tests for AWX single-resource save flows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.capabilities.awx.cli import app
from untaped.capabilities.awx.domain import Resource
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


def test_job_templates_save_translates_fks(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    out = tmp_path / "jt.yml"
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "save",
            "deploy",
            "--out",
            str(out),
            "--organization",
            "Default",
        ],
    )
    assert result.exit_code == 0, result.output
    text = out.read_text()
    assert "kind: JobTemplate" in text
    assert "name: deploy" in text
    assert "playbook: deploy.yml" in text
    # FKs translated to names
    assert "project: playbooks" in text
    assert "inventory: prod" in text


def test_job_templates_save_emits_credentials_from_sub_endpoint(fake_aap: Any) -> None:
    _seed_basic(fake_aap)
    fake_aap.seed("credentials", id=40, name="ssh", organization=1, organization_name="Default")
    fake_aap.seed("credentials", id=41, name="vault", organization=1, organization_name="Default")
    fake_aap.memberships[("job_templates", 30, "credentials")] = {40, 41}

    result = CliInvoker().invoke(
        app, ["job-templates", "save", "deploy", "--organization", "Default"]
    )

    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(result.stdout)
    assert sorted(doc["spec"]["credentials"]) == ["ssh", "vault"]


def test_job_templates_save_default_yaml_round_trips(fake_aap: Any) -> None:
    """Default stdout (no ``--out``, no ``--format``) is a bare YAML
    envelope — a single mapping that ``read_resources`` can ingest
    without ``yaml.safe_load_all`` wrapping. Round-trip into apply
    depends on this shape; using row collection rendering for YAML
    would wrap in a top-level list and silently break it."""
    _seed_basic(fake_aap)
    result = CliInvoker().invoke(
        app, ["job-templates", "save", "deploy", "--organization", "Default"]
    )
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(result.stdout)
    assert isinstance(doc, dict), f"expected bare mapping, got {type(doc).__name__}"
    assert doc["kind"] == "JobTemplate"
    Resource.model_validate(doc)


def test_job_templates_save_apply_round_trips_string_extra_vars(
    fake_aap: Any, tmp_path: Path
) -> None:
    _seed_basic(fake_aap)
    fake_aap.get_record("job_templates", 30)["extra_vars"] = "answer: 42\n"
    save_result = CliInvoker().invoke(
        app, ["job-templates", "save", "deploy", "--organization", "Default"]
    )
    assert save_result.exit_code == 0, save_result.output
    saved = tmp_path / "jt.yml"
    saved.write_text(save_result.stdout)

    fake_aap.get_record("job_templates", 30)["extra_vars"] = "answer: 41\n"
    apply_result = CliInvoker().invoke(app, ["job-templates", "apply", str(saved), "--yes"])

    assert apply_result.exit_code == 0, apply_result.output
    assert "did not converge" not in apply_result.output.lower()
    assert fake_aap.get_record("job_templates", 30)["extra_vars"] == "answer: 42\n"


def test_job_templates_save_format_json_emits_envelope(fake_aap: Any) -> None:
    """``--format json`` emits the single-document envelope as a bare object
    ``{…}`` via ``emit`` (not a one-element list)."""
    _seed_basic(fake_aap)
    result = CliInvoker().invoke(
        app,
        ["job-templates", "save", "deploy", "--organization", "Default", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert isinstance(envelope, dict)
    assert envelope["kind"] == "JobTemplate"
    assert envelope["metadata"]["name"] == "deploy"
    assert envelope["spec"]["playbook"] == "deploy.yml"
    # exclude_none pruning must survive emit (it receives the pre-pruned dict,
    # not the model) — no None leaked back into the rendered spec.
    assert all(value is not None for value in envelope["spec"].values())


def test_job_templates_save_format_raw_emits_kind(fake_aap: Any) -> None:
    """``--format raw`` emits the first key of the envelope per the
    default-column contract. For a Resource that's ``kind``."""
    _seed_basic(fake_aap)
    result = CliInvoker().invoke(
        app, ["job-templates", "save", "deploy", "--organization", "Default", "--format", "raw"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "JobTemplate"


def test_credentials_have_no_save_or_apply(fake_aap: Any) -> None:
    """Credential is read-only — its sub-app should not expose save/apply."""
    result = CliInvoker().invoke(app, ["credentials", "save", "x"])
    assert result.exit_code != 0
    assert "save" in result.output.lower()


def test_save_kind_org_scopes_inventory_child_kind(fake_aap: Any, tmp_path: Path) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("organizations", id=2, name="Other")
    fake_aap.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    fake_aap.seed(
        "inventories",
        id=21,
        name="prod",
        organization=2,
        organization_name="Other",
        kind="",
    )
    fake_aap.seed(
        "hosts",
        id=101,
        name="web-default",
        inventory=20,
        enabled=True,
        summary_fields={"inventory": {"name": "prod", "organization_name": "Default"}},
    )
    fake_aap.seed(
        "hosts",
        id=102,
        name="web-other",
        inventory=21,
        enabled=True,
        summary_fields={"inventory": {"name": "prod", "organization_name": "Other"}},
    )

    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app,
        ["save", "--kind", "hosts", "--org", "Default", "--out-dir", str(out_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (out_dir / "Host__Inventory__Default__prod__web-default.yml").exists()
    assert not (out_dir / "Host__Inventory__Other__prod__web-other.yml").exists()


def test_save_kind_accepts_cli_name(seeded_default_org: Any, tmp_path: Path) -> None:
    """``save --kind job-templates`` should work as well as ``--kind JobTemplate``."""
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app, ["save", "--out-dir", str(out_dir), "--kind", "job-templates"]
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "JobTemplate__Default__deploy.yml").exists()


def test_save_kind_accepts_domain_kind(seeded_default_org: Any, tmp_path: Path) -> None:
    """The ``_resolve_kind`` fallback path: ``--kind JobTemplate`` resolves
    via ``catalog.get`` after ``catalog.by_cli_name`` raises."""
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["save", "--out-dir", str(out_dir), "--kind", "JobTemplate"])
    assert result.exit_code == 0, result.output
    assert (out_dir / "JobTemplate__Default__deploy.yml").exists()


def test_save_kind_rejects_unknown_kind(fake_aap: Any, tmp_path: Path) -> None:
    """Neither ``by_cli_name`` nor ``get`` can resolve a bogus kind —
    the second arm of ``_resolve_kind`` re-raises."""
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["save", "--out-dir", str(out_dir), "--kind", "Bogus"])
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "Bogus" in output


def test_save_kind_print_paths_legacy_shape(seeded_default_org: Any, tmp_path: Path) -> None:
    """``--print-paths`` with ``--kind`` (single-kind path through the
    same loop) keeps the legacy filename-list stdout — proves the flag
    isn't ``--all-kinds``-only."""
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app,
        [
            "save",
            "--out-dir",
            str(out_dir),
            "--kind",
            "job-templates",
            "--print-paths",
        ],
    )
    assert result.exit_code == 0, result.output
    expected = out_dir / "JobTemplate__Default__deploy.yml"
    assert expected.exists()
    assert result.stdout.strip() == str(expected)


def test_save_kind_default_emits_yaml_envelope_on_stdout(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """``save --kind --out-dir`` (no ``--all-kinds``) shares the bulk loop's
    default stdout shape — one ``---``-prefixed envelope per record.
    Coverage gap before this: only ``--all-kinds`` exercised the envelope
    path."""
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app, ["save", "--out-dir", str(out_dir), "--kind", "job-templates"]
    )
    assert result.exit_code == 0, result.output
    docs = [d for d in yaml.safe_load_all(result.stdout) if d is not None]
    assert len(docs) == 1
    Resource.model_validate(docs[0])
    assert docs[0]["kind"] == "JobTemplate"
    assert docs[0]["metadata"]["name"] == "deploy"


def test_job_templates_save_format_with_out_still_writes_yaml_file(
    fake_aap: Any, tmp_path: Path
) -> None:
    """``--out FILE`` takes precedence over ``--format``: the file is
    always YAML (apply-ingestible), even when the user passed
    ``--format json``. Avoids writing a JSON envelope to a ``.yml``
    file that ``apply`` would then fail to parse."""
    _seed_basic(fake_aap)
    out = tmp_path / "jt.yml"
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "save",
            "deploy",
            "--out",
            str(out),
            "--organization",
            "Default",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    text = out.read_text()
    # File body is YAML, not JSON.
    assert text.startswith("kind: JobTemplate")
    Resource.model_validate(yaml.safe_load(text))
    # Stdout is untouched (file write path is the side-effect-only branch).
    assert result.stdout == ""


def test_workflow_save_emits_partial_warning(seeded_default_org: Any, tmp_path: Path) -> None:
    seeded_default_org.seed(
        "workflow_job_templates",
        id=10,
        name="pipeline",
        organization=1,
        organization_name="Default",
        description="multi-step",
    )
    out = tmp_path / "wf.yml"
    result = CliInvoker().invoke(
        app,
        [
            "workflow-templates",
            "save",
            "pipeline",
            "--out",
            str(out),
            "--organization",
            "Default",
        ],
    )
    assert result.exit_code == 0, result.output
    text = out.read_text()
    # The fidelity comment is the first line of the file.
    assert text.startswith("# nodes not saved (v0 limitation)") or text.startswith("# node graph")
    assert "partial save" in result.stderr
