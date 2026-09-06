"""End-to-end CLI tests for AWX bulk save flows."""

from __future__ import annotations

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
    result = CliInvoker().invoke(app, ["save", "--all-kinds", "--out-dir", str(out_dir)])
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
            "save",
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


def test_save_all_filter_skips_schedules_when_filter_field_absent(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Schedule's API has no ``organization`` field, so AWX would 400 on
    ``?organization__name=…``. Bulk save must detect that the filter
    references a field this kind doesn't have, skip the kind with a
    stderr warning, and continue with the kinds that do support it."""
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
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
    result = CliInvoker().invoke(
        app,
        [
            "save",
            "--all-kinds",
            "--out-dir",
            str(out_dir),
            "--filter",
            "organization__name=Default",
        ],
    )
    assert result.exit_code == 0, result.output
    output = result.output + (result.stderr or "")
    assert "skipping Schedule" in output
    # Org-scoped kinds were saved; Schedule was not.
    assert (out_dir / "JobTemplate__Default__deploy.yml").exists()
    assert not list(out_dir.glob("Schedule__*.yml"))


def test_save_all_org_scopes_direct_org_kinds(seeded_default_org: Any, tmp_path: Path) -> None:
    seeded_default_org.seed("organizations", id=2, name="Other")
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks-default",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "projects",
        id=11,
        name="playbooks-other",
        organization=2,
        organization_name="Other",
        scm_type="git",
    )
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy-default",
        organization=1,
        organization_name="Default",
        playbook="deploy.yml",
        project=10,
        project_name="playbooks-default",
    )
    seeded_default_org.seed(
        "job_templates",
        id=31,
        name="deploy-other",
        organization=2,
        organization_name="Other",
        playbook="deploy.yml",
        project=11,
        project_name="playbooks-other",
    )

    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app,
        ["save", "--all-kinds", "--org", "Default", "--out-dir", str(out_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (out_dir / "Project__Default__playbooks-default.yml").exists()
    assert (out_dir / "JobTemplate__Default__deploy-default.yml").exists()
    assert not (out_dir / "Project__Other__playbooks-other.yml").exists()
    assert not (out_dir / "JobTemplate__Other__deploy-other.yml").exists()


def test_save_all_org_includes_matching_schedules_without_invalid_filter(
    fake_aap: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("organizations", id=2, name="Other")
    fake_aap.seed(
        "job_templates",
        id=30,
        name="deploy-default",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    fake_aap.seed(
        "job_templates",
        id=31,
        name="deploy-other",
        organization=2,
        organization_name="Other",
        playbook="b.yml",
    )
    fake_aap.seed(
        "schedules",
        id=50,
        name="default-nightly",
        unified_job_template=30,
        rrule="DTSTART:20230101T000000Z RRULE:FREQ=DAILY",
        enabled=True,
        summary_fields={
            "unified_job_template": {
                "id": 30,
                "name": "deploy-default",
                "unified_job_type": "job_template",
                "organization_name": "Default",
            }
        },
    )
    fake_aap.seed(
        "schedules",
        id=51,
        name="other-nightly",
        unified_job_template=31,
        rrule="DTSTART:20230101T000000Z RRULE:FREQ=DAILY",
        enabled=True,
        summary_fields={
            "unified_job_template": {
                "id": 31,
                "name": "deploy-other",
                "unified_job_type": "job_template",
                "organization_name": "Other",
            }
        },
    )
    original_list = fake_aap._list
    schedule_params: list[dict[str, str]] = []

    def spy_list(api_path: str, params: dict[str, str]) -> Any:
        if api_path == "schedules":
            schedule_params.append(dict(params))
            assert "organization__name" not in params
            assert "inventory__organization__name" not in params
        return original_list(api_path, params)

    monkeypatch.setattr(fake_aap, "_list", spy_list)

    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app,
        ["save", "--all-kinds", "--org", "Default", "--out-dir", str(out_dir)],
    )

    assert result.exit_code == 0, result.output
    assert schedule_params
    assert (
        out_dir / "Schedule__JobTemplate__Default__deploy-default__default-nightly.yml"
    ).exists()
    assert not (out_dir / "Schedule__JobTemplate__Other__deploy-other__other-nightly.yml").exists()


def test_save_all_org_rejects_duplicate_raw_organization_filter(
    fake_aap: Any, tmp_path: Path
) -> None:
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app,
        [
            "save",
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


def test_save_all_filter_passes_through_read_only_field(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Read-only fields (``modified``, ``created``, ``last_job_status``)
    are valid AWX list filters even though they aren't accepted on writes.
    A time-windowed backup like ``--filter modified__gte=…`` must pass
    through, not get short-circuited as "field not on this kind"."""
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
            "--all-kinds",
            "--out-dir",
            str(out_dir),
            "--filter",
            "modified__gte=2024-01-01",
        ],
    )
    assert result.exit_code == 0, result.output
    output = result.output + (result.stderr or "")
    # Read-only fields like `modified` must not be pre-rejected by the
    # filter-applicability check — they're valid AWX list filters even
    # though they aren't accepted on writes.
    assert "filter field 'modified'" not in output


def test_save_all_filter_passes_through_list_only_field(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """``last_job_status`` is a real AWX field exposed in JobTemplate's
    ``list_columns`` but not enumerated in ``canonical_fields`` or
    ``read_only_fields``. A status-scoped backup must not pre-reject
    the kind that actually exposes the field — that would silently
    empty the most likely use case for the flag. Other kinds (Project,
    Schedule, …) legitimately don't have ``last_job_status`` and may
    still be skipped."""
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
            "--all-kinds",
            "--out-dir",
            str(out_dir),
            "--filter",
            "last_job_status=successful",
        ],
    )
    assert result.exit_code == 0, result.output
    output = result.output + (result.stderr or "")
    # JobTemplate exposes last_job_status (in its list_columns) — it must
    # NOT be in the skip list. Other kinds without the field may be skipped.
    assert "JobTemplate: filter field 'last_job_status'" not in output


def test_save_all_with_no_filter_captures_every_kind(
    seeded_default_org: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bulk save with no ``--filter`` is "back up everything": JTs across
    every org plus parent-scoped kinds (Schedule). ``default_organization``
    must not silently narrow the backup — it's a name-disambiguation hint
    for ``get``/``launch``/``update``, not a save scope."""
    monkeypatch.setenv("UNTAPED_AWX__DEFAULT_ORGANIZATION", "Default")
    seeded_default_org.seed("organizations", id=2, name="Other")
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy-default",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    seeded_default_org.seed(
        "job_templates",
        id=31,
        name="deploy-other",
        organization=2,
        organization_name="Other",
        playbook="b.yml",
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
                "name": "deploy-default",
                "unified_job_type": "job_template",
                "organization_name": "Default",
            }
        },
    )

    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["save", "--all-kinds", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output

    saved_jts = sorted(p.name for p in out_dir.glob("JobTemplate__*.yml"))
    assert saved_jts == [
        "JobTemplate__Default__deploy-default.yml",
        "JobTemplate__Other__deploy-other.yml",
    ], f"expected both JTs saved, got {saved_jts}"
    assert list(out_dir.glob("Schedule__*.yml")), (
        "schedule excluded from no-filter backup; "
        f"saved files: {[p.name for p in out_dir.iterdir()]}"
    )


def test_save_all_filter_rejects_malformed_entry(fake_aap: Any, tmp_path: Path) -> None:
    """Same KEY=VALUE validation as ``<kind> list --filter``."""
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(
        app, ["save", "--all-kinds", "--out-dir", str(out_dir), "--filter", "bogus"]
    )
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "KEY=VALUE" in output


def test_save_all_distinguishes_same_named_resources_across_orgs(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Two same-named org-scoped resources in different orgs must produce two distinct files."""
    seeded_default_org.seed("organizations", id=2, name="Other")
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    seeded_default_org.seed(
        "job_templates",
        id=31,
        name="deploy",  # same name, different org
        organization=2,
        organization_name="Other",
        playbook="b.yml",
    )

    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["save", "--all-kinds", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output

    saved = sorted(p.name for p in out_dir.glob("JobTemplate__*.yml"))
    assert saved == [
        "JobTemplate__Default__deploy.yml",
        "JobTemplate__Other__deploy.yml",
    ], f"expected two distinct files for same-named JTs in different orgs, got {saved}"


def test_save_all_default_emits_yaml_envelopes_on_stdout(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Default ``save --all-kinds`` stdout shape is a multi-doc YAML stream of
    envelopes (one per written resource) so the bulk dump pipes straight
    into ``apply``. Files on disk are unchanged from today."""
    seeded_default_org.seed("organizations", id=2, name="Other")
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy-default",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    seeded_default_org.seed(
        "job_templates",
        id=31,
        name="deploy-other",
        organization=2,
        organization_name="Other",
        playbook="b.yml",
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["save", "--all-kinds", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output

    files = sorted(out_dir.glob("JobTemplate__*.yml"))
    assert len(files) == 2

    docs = [d for d in yaml.safe_load_all(result.stdout) if d is not None]
    assert len(docs) == len(files), (
        f"expected one stdout envelope per written file, got {len(docs)} docs "
        f"for {len(files)} files"
    )
    for doc in docs:
        assert isinstance(doc, dict)
        Resource.model_validate(doc)
        assert doc["kind"] == "JobTemplate"
    names = sorted(d["metadata"]["name"] for d in docs)
    assert names == ["deploy-default", "deploy-other"]


def test_save_all_print_paths_emits_filenames_on_stdout(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """``--print-paths`` emits one written-file path per line, no envelopes."""
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
        app, ["save", "--all-kinds", "--out-dir", str(out_dir), "--print-paths"]
    )
    assert result.exit_code == 0, result.output
    expected = out_dir / "JobTemplate__Default__deploy.yml"
    assert expected.exists()
    stdout_lines = [line for line in result.stdout.splitlines() if line]
    assert stdout_lines == [str(expected)]
    # Negative: no envelope content leaked onto stdout under --print-paths.
    assert "kind:" not in result.stdout
    assert "metadata:" not in result.stdout


def test_save_all_default_coexists_with_read_only_skip_notes(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Read-only skip notes go to stderr; they must not corrupt the
    multi-doc YAML stream on stdout. Seeds a Credential (read-only,
    skipped) alongside a Project so the loop hits both branches."""
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
    result = CliInvoker().invoke(app, ["save", "--all-kinds", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert "skipping Credential" in result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d is not None]
    # Only the Project envelope should be on stdout; Credential was skipped.
    assert len(docs) == 1
    Resource.model_validate(docs[0])
    assert docs[0]["kind"] == "Project"


def test_save_all_default_keeps_partial_fidelity_header_comment(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    """Partial-fidelity kinds (WorkflowJobTemplate) carry an inline
    ``# fidelity-note`` header in the saved YAML. That comment must
    survive into the multi-doc stdout stream so the stream is
    byte-identical to the files it shadows — and ``yaml.safe_load_all``
    must still parse it (``#`` is a YAML comment, but tests cement the
    contract)."""
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
        app, ["save", "--all-kinds", "--out-dir", str(out_dir), "--kind", "WorkflowJobTemplate"]
    )
    assert result.exit_code == 0, result.output
    # The disk file's first non-separator line is the comment; that
    # exact line must reappear verbatim in the stdout stream so the
    # bulk dump matches the on-disk shape per doc (modulo trailing
    # newline added by the CLI output helper).
    saved = out_dir / "WorkflowJobTemplate__Default__pipeline.yml"
    file_text = saved.read_text()
    first_comment_line = next(line for line in file_text.splitlines() if line.startswith("#"))
    assert first_comment_line in result.stdout, (
        "header_comment in file does not appear in stdout stream"
    )
    # Stream still parses despite the embedded comment.
    docs = [d for d in yaml.safe_load_all(result.stdout) if d is not None]
    assert len(docs) == 1
    assert docs[0]["kind"] == "WorkflowJobTemplate"


def test_save_all_expands_tilde_in_out_dir(
    seeded_default_org: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--out-dir ~/dump`` must expand on both ``mkdir`` and the
    per-record write. A regression that mkdir's the literal path while
    write_text targets the expanded one leaves the files unwritten or
    in two places."""
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
        app, ["save", "--all-kinds", "--out-dir", "~/backup", "--print-paths"]
    )
    assert result.exit_code == 0, result.output
    expanded = tmp_path / "backup" / "JobTemplate__Default__deploy.yml"
    backup_dir = tmp_path / "backup"
    actual = list(backup_dir.iterdir()) if backup_dir.exists() else "no backup dir"
    assert expanded.exists(), f"expected file at {expanded}, found: {actual}"
    # No literal ``./~/...`` directory created in cwd.
    assert not Path("~/backup").exists()


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
    result = CliInvoker().invoke(app, ["save", "--all-kinds", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert result.stdout == "", f"expected empty stdout, got: {result.stdout!r}"
    assert "skipping Credential" in result.stderr


def test_save_all_skips_credentials(seeded_default_org: Any, tmp_path: Path) -> None:
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
    result = CliInvoker().invoke(
        app,
        ["save", "--all-kinds", "--out-dir", str(out_dir)],
    )
    assert result.exit_code == 0, result.output
    # Project file exists; Credential file does not.
    assert (out_dir / "Project__Default__playbooks.yml").exists()
    assert not any(p.name.startswith("Credential__") for p in out_dir.iterdir())
    assert "skipping Credential" in result.stderr


def test_save_all_kinds_flag(
    seeded_default_org: Any,
    tmp_path: Path,
) -> None:
    """`--all-kinds` is the only top-level all-kinds selector."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    out_dir = tmp_path / "backup"
    result = CliInvoker().invoke(app, ["save", "--all-kinds", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert (out_dir / "Project__Default__playbooks.yml").exists()
    assert "deprecated" not in result.stdout
    assert "deprecated" not in result.stderr


def test_save_rejects_removed_all_alias() -> None:
    result = CliInvoker().invoke(app, ["save", "--all", "--out-dir", "backup"])
    assert result.exit_code != 0
    assert "--all" in result.output
