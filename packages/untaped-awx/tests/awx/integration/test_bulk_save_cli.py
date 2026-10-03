"""End-to-end CLI tests for AWX bulk save flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.testing import CliInvoker
from untaped_awx.cli import app
from untaped_awx.domain import Resource


def test_save_all_rejects_traversal_in_resource_names(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Resource names with `/` or `..` must not produce dangerous filesystem paths."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="evil/../escape",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["export", "--all-kinds", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output

    # No nested directories produced by stray `/`
    nested_dirs = [p for p in out_dir.rglob("*") if p.is_dir()]
    assert nested_dirs == [], f"sanitization left nested dirs: {nested_dirs}"

    # All written files live directly in out_dir.
    written = list(out_dir.rglob("*.yml"))
    assert len(written) == 1, written
    target = written[0]
    assert target.parent.resolve() == out_dir.resolve()
    # The literal name on disk must not contain path separators.
    assert "/" not in target.name and "\\" not in target.name
    # Original name preserved inside the YAML metadata.
    assert "evil/../escape" in target.read_text()


def test_save_all_filter_scopes_org_kinds_server_side(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """`save --all-kinds --filter organization__name=X` is passed verbatim to AWX
    for every saved kind, so org-scoped kinds (JT, Project) get filtered
    server-side and other-org records don't leak through."""
    seeded_default_org.seed("organizations", id=2, name="Other")
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="deploy.yml",
        project=10,
        project_name="playbooks",
    )
    # Same JT name, different org — must be excluded by `--filter organization__name=Default`.
    seeded_default_org.seed(
        "job_templates",
        id=31,
        name="deploy-elsewhere",
        organization=2,
        organization_name="Other",
        playbook="deploy.yml",
        project=10,
        project_name="playbooks",
    )

    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app,
        [
            "export",
            "--all-kinds",
            "--out-dir",
            str(out_dir),
            "--filter",
            "organization__name=Default",
        ],
    )
    assert result.exit_code == 0, result.output

    assert (out_dir / "JobTemplate__Default__deploy.yml").exists()
    assert (out_dir / "Project__Default__playbooks.yml").exists()
    other_org_jt_files = [
        p for p in out_dir.glob("JobTemplate__*.yml") if "deploy-elsewhere" in p.name
    ]
    assert not other_org_jt_files, (
        "different-org JT leaked through bulk-save filter; saved files: "
        f"{[p.name for p in out_dir.iterdir()]}"
    )


def test_save_all_org_rejects_duplicate_raw_organization_filter(
    fake_aap: Any, tmp_path: Path
) -> None:
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app,
        [
            "export",
            "--all-kinds",
            "--org",
            "Default",
            "--filter",
            "organization__name=Default",
            "--out-dir",
            str(out_dir),
        ],
    )

    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "--org" in output
    assert "organization__name" in output


def test_save_all_filter_rejects_malformed_entry(fake_aap: Any, tmp_path: Path) -> None:
    """Same KEY=VALUE validation as ``<kind> list --filter``."""
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app, ["export", "--all-kinds", "--out-dir", str(out_dir), "--filter", "bogus"]
    )
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "KEY=VALUE" in output


def test_save_all_writes_workflows_with_their_nodes_and_no_header(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Workflows export at full fidelity: the file has no fidelity header,
    carries ``spec.nodes``, and the stdout stream repeats the file."""
    seeded_default_org.seed(
        "workflow_job_templates",
        id=10,
        name="pipeline",
        organization=1,
        organization_name="Default",
        description="multi-step",
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app, ["export", "--all-kinds", "--out-dir", str(out_dir), "--kind", "WorkflowJobTemplate"]
    )
    assert result.exit_code == 0, result.output
    saved = out_dir / "WorkflowJobTemplate__Default__pipeline.yml"
    file_text = saved.read_text()
    assert not any(line.startswith("#") for line in file_text.splitlines())
    assert file_text in result.stdout
    docs = [d for d in yaml.safe_load_all(result.stdout) if d is not None]
    assert len(docs) == 1
    assert docs[0]["kind"] == "WorkflowJobTemplate"
    assert docs[0]["spec"]["nodes"] == []


def test_save_all_with_only_read_only_kinds_emits_empty_stream(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """A bulk save where every present kind is read-only (or absent)
    yields an empty stdout stream — not a crash, not a stray ``---``
    separator. Pins the loop's behaviour when no record is dumped."""
    seeded_default_org.seed(
        "credentials",
        id=20,
        name="ssh-key",
        organization=1,
        organization_name="Default",
        credential_type=1,
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["export", "--all-kinds", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert result.stdout == "", f"expected empty stdout, got: {result.stdout!r}"
    assert "skipping Credential" in result.stderr


def test_save_all_without_filter_backs_up_every_org_and_streams_envelopes(
    seeded_default_org: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``--filter`` means "back up everything": ``default_organization`` is
    a name-disambiguation hint, not a save scope. Same-named resources in two
    orgs get distinct files, and stdout carries one envelope per written file
    so the dump pipes straight into ``apply``."""
    monkeypatch.setenv("UNTAPED_AWX__DEFAULT_ORGANIZATION", "Default")
    seeded_default_org.seed("organizations", id=2, name="Other")
    for id_, org, org_name in ((30, 1, "Default"), (31, 2, "Other")):
        seeded_default_org.seed(
            "job_templates",
            id=id_,
            name="deploy",
            organization=org,
            organization_name=org_name,
            playbook="a.yml",
        )
    seeded_default_org.seed(
        "schedules",
        id=50,
        name="nightly",
        unified_job_template=30,
        rrule="DTSTART:20230101T000000Z RRULE:FREQ=DAILY",
        enabled=True,
        summary_fields={
            "unified_job_template": {
                "id": 30,
                "name": "deploy",
                "unified_job_type": "job_template",
                "organization_name": "Default",
            }
        },
    )

    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["export", "--all-kinds", "--out-dir", str(out_dir)])

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "JobTemplate__Default__deploy.yml",
        "JobTemplate__Other__deploy.yml",
        "Schedule__JobTemplate__Default__deploy__nightly.yml",
    ]
    docs = [d for d in yaml.safe_load_all(result.stdout) if d is not None]
    assert sorted(Resource.model_validate(d).kind for d in docs) == [
        "JobTemplate",
        "JobTemplate",
        "Schedule",
    ]


def test_save_all_skips_read_only_kinds_on_stderr_only(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Read-only skip notes go to stderr and never corrupt the stdout stream."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "credentials",
        id=20,
        name="ssh-key",
        organization=1,
        organization_name="Default",
        credential_type=1,
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["export", "--all-kinds", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert [p.name for p in out_dir.iterdir()] == ["Project__Default__playbooks.yml"]
    assert "skipping Credential" in result.stderr
    assert "deprecated" not in result.output
    docs = [d for d in yaml.safe_load_all(result.stdout) if d is not None]
    assert [Resource.model_validate(d).kind for d in docs] == ["Project"]


def test_save_all_print_paths_expands_tilde_in_out_dir(
    seeded_default_org: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--out-dir ~/dump`` expands for both ``mkdir`` and every write, and
    ``--print-paths`` prints one written path per line instead of envelopes."""
    monkeypatch.setenv("HOME", str(tmp_path))
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    result = CliInvoker().invoke(
        app, ["export", "--all-kinds", "--out-dir", "~/backup", "--print-paths"]
    )
    assert result.exit_code == 0, result.output
    expanded = tmp_path / "backup" / "JobTemplate__Default__deploy.yml"
    assert expanded.exists()
    assert result.stdout.splitlines() == [str(expanded)]
    assert not Path("~/backup").exists()


def test_bulk_export_comment_in_every_file(seeded_default_org: Any, tmp_path: Path) -> None:
    fake_aap = seeded_default_org
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    for jt_id, name in ((30, "deploy"), (31, "rollback")):
        fake_aap.seed(
            "job_templates",
            id=jt_id,
            name=name,
            organization=1,
            organization_name="Default",
            project=10,
            project_name="playbooks",
            playbook=f"{name}.yml",
        )
    out_dir = tmp_path / "out"
    result = CliInvoker().invoke(
        app, ["export", "--kind", "job-templates", "--out-dir", str(out_dir), "--comment", "x"]
    )
    assert result.exit_code == 0, result.output
    files = list(out_dir.glob("*.yml"))
    assert len(files) == 2 and all(f.read_text().startswith("# x\n") for f in files)
