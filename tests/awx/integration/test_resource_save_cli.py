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


def _save_to(out: str | None, *extra: str) -> Any:
    args = ["job-templates", "export", "deploy", "--organization", "Default", *extra]
    return CliInvoker().invoke(app, args if out is None else [*args, f"--out={out}"])


def test_save_out_writes_through_a_symlink(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    target = tmp_path / "real.yml"
    target.write_text("old\n")
    link = tmp_path / "link.yml"
    link.symlink_to(target)

    result = _save_to(str(link))

    assert result.exit_code == 0, result.output
    assert link.is_symlink()
    assert "kind: JobTemplate" in target.read_text()


def test_save_out_dash_writes_stdout(fake_aap: Any) -> None:
    _seed_basic(fake_aap)
    result = _save_to("-")
    assert result.exit_code == 0, result.output
    assert "kind: JobTemplate" in result.stdout


def test_save_out_writes_to_a_fifo(fake_aap: Any, tmp_path: Path) -> None:
    import os
    import threading

    _seed_basic(fake_aap)
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    received: list[str] = []
    reader = threading.Thread(target=lambda: received.append(fifo.read_text()), daemon=True)
    reader.start()

    result = _save_to(str(fifo))
    reader.join(5)

    assert result.exit_code == 0, result.output
    assert "kind: JobTemplate" in received[0]
    assert not fifo.is_file()


def test_save_out_reports_unwritable_paths(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    blocker = tmp_path / "file"
    blocker.write_text("")

    result = _save_to(str(blocker / "out.yml"))

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    assert "out.yml" in result.stderr


def test_job_templates_save_apply_round_trips_string_extra_vars(
    fake_aap: Any, tmp_path: Path
) -> None:
    _seed_basic(fake_aap)
    fake_aap.get_record("job_templates", 30)["extra_vars"] = "answer: 42\n"
    save_result = CliInvoker().invoke(
        app, ["job-templates", "export", "deploy", "--organization", "Default"]
    )
    assert save_result.exit_code == 0, save_result.output
    saved = tmp_path / "jt.yml"
    saved.write_text(save_result.stdout)

    fake_aap.get_record("job_templates", 30)["extra_vars"] = "answer: 41\n"
    apply_result = CliInvoker().invoke(app, ["job-templates", "apply", str(saved), "--yes"])

    assert apply_result.exit_code == 0, apply_result.output
    assert "did not converge" not in apply_result.output.lower()
    assert fake_aap.get_record("job_templates", 30)["extra_vars"] == "answer: 42\n"


def test_credentials_have_no_save_or_apply(fake_aap: Any) -> None:
    """Credential is read-only — its sub-app should not expose save/apply."""
    result = CliInvoker().invoke(app, ["credentials", "export", "x"])
    assert result.exit_code != 0
    assert "export" in result.output.lower()


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
        ["export", "--kind", "hosts", "--org", "Default", "--out-dir", str(out_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (out_dir / "Host__Inventory__Default__prod__web-default.yml").exists()
    assert not (out_dir / "Host__Inventory__Other__prod__web-other.yml").exists()


def test_save_kind_rejects_unknown_kind(fake_aap: Any, tmp_path: Path) -> None:
    """Neither ``by_cli_name`` nor ``get`` can resolve a bogus kind —
    the second arm of ``_resolve_kind`` re-raises."""
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["export", "--out-dir", str(out_dir), "--kind", "Bogus"])
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "Bogus" in output


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
            "export",
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


def test_job_templates_export_out_writes_yaml_with_fk_names(fake_aap: Any, tmp_path: Path) -> None:
    """``--out FILE`` always writes apply-ingestible YAML, even with ``--format json``."""
    _seed_basic(fake_aap)
    out = tmp_path / "jt.yml"
    result = _save_to(str(out), "--format", "json")
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    text = out.read_text()
    assert text.startswith("kind: JobTemplate")
    spec = Resource.model_validate(yaml.safe_load(text)).spec
    assert (spec["playbook"], spec["project"], spec["inventory"]) == (
        "deploy.yml",
        "playbooks",
        "prod",
    )


def test_job_templates_export_default_is_one_bare_yaml_envelope(fake_aap: Any) -> None:
    """Default stdout is a bare mapping (not a one-item list) so it round-trips
    into ``apply``; multi-valued FKs come from their sub-endpoint as names."""
    _seed_basic(fake_aap)
    fake_aap.seed("credentials", id=40, name="ssh", organization=1, organization_name="Default")
    fake_aap.seed("credentials", id=41, name="vault", organization=1, organization_name="Default")
    fake_aap.memberships[("job_templates", 30, "credentials")] = {40, 41}

    result = _save_to(None)

    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(result.stdout)
    assert isinstance(doc, dict)
    assert Resource.model_validate(doc).kind == "JobTemplate"
    assert sorted(doc["spec"]["credentials"]) == ["ssh", "vault"]


def test_job_templates_export_format_json_and_raw(fake_aap: Any) -> None:
    """``json`` is the bare pruned envelope; ``raw`` prints its first key, ``kind``."""
    _seed_basic(fake_aap)
    envelope = json.loads(_save_to(None, "--format", "json").stdout)
    assert envelope["metadata"]["name"] == "deploy"
    assert all(value is not None for value in envelope["spec"].values())
    assert _save_to(None, "--format", "raw").stdout.strip() == "JobTemplate"


@pytest.mark.parametrize("kind", ["job-templates", "JobTemplate"])
def test_export_kind_accepts_cli_and_domain_names(
    seeded_default_org: Any, tmp_path: Path, kind: str
) -> None:
    """``export --kind`` writes one file per record and streams its envelope."""
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["export", "--out-dir", str(out_dir), "--kind", kind])
    assert result.exit_code == 0, result.output
    assert [p.name for p in out_dir.iterdir()] == ["JobTemplate__Default__deploy.yml"]
    docs = [d for d in yaml.safe_load_all(result.stdout) if d is not None]
    assert [Resource.model_validate(d).metadata.name for d in docs] == ["deploy"]
