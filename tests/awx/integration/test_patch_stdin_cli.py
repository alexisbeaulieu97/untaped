"""End-to-end CLI tests for ``untaped awx <kind> patch --stdin`` mass-patch.

Drives the per-kind ``apply`` command from a piped selection (names, ids, or a
``--format pipe`` envelope stream) plus a ``--set`` / ``--patch-file`` overlay.
Pins the contract: explicit dry-run previews, ``--yes`` writes a sparse PATCH of only
the overlaid fields, the selection path never creates, and an overlay field the
tool does not recognize is passed through with a warning and then verified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def _seed_jt(fake: Any, *, id_: int = 30, name: str = "deploy", **extra: Any) -> None:
    fake.seed(
        "job_templates",
        id=id_,
        name=name,
        organization=1,
        organization_name="Default",
        playbook="deploy.yml",
        **extra,
    )


def _seed_project(fake: Any, *, id_: int = 50, name: str = "playbooks", **extra: Any) -> None:
    fake.seed(
        "projects",
        id=id_,
        name=name,
        organization=1,
        organization_name="Default",
        scm_type="git",
        **extra,
    )


def _patches(fake: Any) -> list[Any]:
    return [c for c in fake.router.calls if c.request.method == "PATCH"]


def _posts(fake: Any) -> list[Any]:
    return [c for c in fake.router.calls if c.request.method == "POST"]


def test_patch_stdin_preview_does_not_write(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "verbosity=2",
            "--organization",
            "Default",
            "--dry-run",
        ],
        input="deploy\n",
    )
    assert result.exit_code == 0, result.output
    assert _patches(seeded_default_org) == []
    assert "verbosity" in (result.stderr or "")


def test_patch_stdin_yes_patches_only_set_field(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "verbosity=2",
            "--yes",
            "--organization",
            "Default",
        ],
        input="deploy\n",
    )
    assert result.exit_code == 0, result.output
    patches = _patches(seeded_default_org)
    assert len(patches) == 1
    assert json.loads(patches[0].request.content) == {"verbosity": 2}
    assert seeded_default_org.get_record("job_templates", 30)["verbosity"] == 2


def test_patch_stdin_never_creates_missing_target(seeded_default_org: Any) -> None:
    """A piped name that doesn't resolve is a per-item error — never a create."""
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "verbosity=2",
            "--yes",
            "--organization",
            "Default",
        ],
        input="ghost\n",
    )
    assert result.exit_code == 1
    assert "ghost" in (result.stderr or result.output)
    assert _posts(seeded_default_org) == []  # nothing created


def test_patch_stdin_requires_set_or_patch_file(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org)
    result = CliInvoker().invoke(
        app, ["job-templates", "patch", "--stdin", "--yes"], input="deploy\n"
    )
    assert result.exit_code == 1
    assert "--set" in (result.stderr or result.output)


def test_apply_file_with_set_is_usage_error(seeded_default_org: Any, tmp_path: Path) -> None:
    f = tmp_path / "x.yml"
    f.write_text("kind: JobTemplate\nmetadata: {name: deploy}\nspec: {}\n")
    result = CliInvoker().invoke(app, ["job-templates", "apply", str(f), "--set", "verbosity=2"])
    assert result.exit_code == 2
    assert "--set" in (result.stderr or result.output)


def test_apply_neither_file_nor_stdin_is_usage_error(seeded_default_org: Any) -> None:
    result = CliInvoker().invoke(app, ["job-templates", "apply"])
    assert result.exit_code == 2
    assert "requires an argument" in (result.stderr or result.output)


def test_patch_stdin_patch_file_merges_with_set(seeded_default_org: Any, tmp_path: Path) -> None:
    _seed_jt(seeded_default_org)
    pf = tmp_path / "p.yml"
    pf.write_text("verbosity: 1\njob_tags: base\n")
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--patch-file",
            str(pf),
            "--set",
            "verbosity=5",
            "--yes",
            "--organization",
            "Default",
        ],
        input="deploy\n",
    )
    assert result.exit_code == 0, result.output
    # --set overrides the patch-file's verbosity; job_tags from the file remains.
    assert json.loads(_patches(seeded_default_org)[0].request.content) == {
        "verbosity": 5,
        "job_tags": "base",
    }


def test_patch_stdin_by_id(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org, id_=77, name="byid")
    result = CliInvoker().invoke(
        app,
        ["job-templates", "patch", "--stdin", "--by-id", "--set", "verbosity=4", "--yes"],
        input="77\n",
    )
    assert result.exit_code == 0, result.output
    assert seeded_default_org.get_record("job_templates", 77)["verbosity"] == 4


def test_patch_stdin_unchanged_does_not_patch(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org, verbosity=2)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "verbosity=2",
            "--yes",
            "--organization",
            "Default",
        ],
        input="deploy\n",
    )
    assert result.exit_code == 0, result.output
    assert _patches(seeded_default_org) == []
    assert "unchanged" in result.stdout.lower()


def test_patch_stdin_consumes_pipe_envelope(seeded_default_org: Any) -> None:
    """`list --format pipe | patch --stdin` — the headline pipeline."""
    _seed_jt(seeded_default_org)
    envelope = json.dumps(
        {"untaped": "1", "kind": "awx.job_template", "record": {"id": 30, "name": "deploy"}}
    )
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "verbosity=9",
            "--yes",
            "--organization",
            "Default",
        ],
        input=envelope + "\n",
    )
    assert result.exit_code == 0, result.output
    assert seeded_default_org.get_record("job_templates", 30)["verbosity"] == 9


def test_patch_stdin_project_default_environment_fk(seeded_default_org: Any) -> None:
    """Regression: ``projects patch --stdin --set default_environment=<ee>`` —
    the EE name resolves to an id and lands in the sparse PATCH. Was rejected as
    an unknown field because ``default_environment`` was absent from the spec."""
    _seed_project(seeded_default_org, default_environment=None)
    seeded_default_org.seed("execution_environments", id=9, name="prod-ee")
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "patch",
            "--stdin",
            "--set",
            "default_environment=prod-ee",
            "--yes",
            "--organization",
            "Default",
        ],
        input="playbooks\n",
    )
    assert result.exit_code == 0, result.output
    patches = _patches(seeded_default_org)
    assert len(patches) == 1
    assert json.loads(patches[0].request.content) == {"default_environment": 9}  # name→id
    assert seeded_default_org.get_record("projects", 50)["default_environment"] == 9


def test_patch_stdin_project_default_environment_preview(seeded_default_org: Any) -> None:
    """Preview (no ``--yes``) shows the ``default_environment`` diff, writes nothing."""
    _seed_project(seeded_default_org, default_environment=None)
    seeded_default_org.seed("execution_environments", id=9, name="prod-ee")
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "patch",
            "--stdin",
            "--dry-run",
            "--set",
            "default_environment=prod-ee",
            "--organization",
            "Default",
        ],
        input="playbooks\n",
    )
    assert result.exit_code == 0, result.output
    assert _patches(seeded_default_org) == []
    assert "default_environment" in (result.stderr or "")


def test_patch_stdin_warns_and_passes_through_unknown_field(seeded_default_org: Any) -> None:
    """Passthrough model: a field this tool doesn't recognize is sent to AWX
    as-is, with a soft warning (was a hard exit-2 rejection under the old
    closed allowlist). NOTE: the fake server blindly stores the body, so this
    proves the CLI *sends* the field — not that a real AWX accepts it."""
    _seed_jt(seeded_default_org)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "zzz_bogus=1",
            "--yes",
            "--organization",
            "Default",
        ],
        input="deploy\n",
    )
    assert result.exit_code == 0, result.output
    assert "zzz_bogus" in (result.stderr or "")  # warned, not rejected
    patches = _patches(seeded_default_org)
    assert len(patches) == 1
    assert json.loads(patches[0].request.content) == {"zzz_bogus": 1}  # passed through


def test_patch_stdin_ignored_unknown_field_fails_by_default(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org)
    seeded_default_org.ignored_write_fields.add("zzz_bogus")
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "zzz_bogus=1",
            "--yes",
            "--organization",
            "Default",
        ],
        input="deploy\n",
    )
    output = result.output + (result.stderr or "")
    assert result.exit_code == 1, output
    assert "zzz_bogus" in output
    assert "unverified" in output
    assert "zzz_bogus" not in seeded_default_org.get_record("job_templates", 30)


def test_patch_stdin_ignored_unknown_field_can_be_allowed(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org)
    seeded_default_org.ignored_write_fields.add("zzz_bogus")
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "zzz_bogus=1",
            "--yes",
            "--allow-unverified",
            "--organization",
            "Default",
        ],
        input="deploy\n",
    )
    output = result.output + (result.stderr or "")
    assert result.exit_code == 0, output
    assert "updated" in result.stdout
    assert "zzz_bogus" in output
    assert "unverified" in output
    assert "zzz_bogus" not in seeded_default_org.get_record("job_templates", 30)


def test_patch_stdin_allow_unverified_requires_yes(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "zzz_bogus=1",
            "--allow-unverified",
            "--organization",
            "Default",
        ],
        input="deploy\n",
    )
    assert result.exit_code == 1
    assert "--yes" in (result.output + (result.stderr or ""))


def test_patch_stdin_overlay_fks_use_organization_scope(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="OrgA")
    fake_aap.seed("organizations", id=2, name="OrgB")
    fake_aap.seed("projects", id=10, name="playbooks", organization=1, organization_name="OrgA")
    fake_aap.seed("projects", id=11, name="playbooks", organization=2, organization_name="OrgB")
    fake_aap.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="OrgA",
        project=11,
        playbook="deploy.yml",
    )

    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "patch",
            "--stdin",
            "--set",
            "project=playbooks",
            "--yes",
            "--organization",
            "OrgA",
        ],
        input="deploy\n",
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    patches = _patches(fake_aap)
    assert len(patches) == 1
    assert json.loads(patches[0].request.content) == {"project": 10}


def test_patch_stdin_inventory_child_fks_use_inventory_scope(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("inventories", id=20, name="prod", organization=1, organization_name="Default")
    fake_aap.seed("inventories", id=21, name="staging", organization=1, organization_name="Default")
    fake_aap.seed("groups", id=200, name="web", inventory=20, inventory_name="prod")
    fake_aap.seed("hosts", id=101, name="web-01", inventory=20, inventory_name="prod")
    fake_aap.seed("hosts", id=102, name="web-01", inventory=21, inventory_name="staging")

    result = CliInvoker().invoke(
        app,
        [
            "groups",
            "patch",
            "--stdin",
            "--set",
            'hosts=["web-01"]',
            "--yes",
            "--inventory",
            "prod",
        ],
        input="web\n",
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    assert fake_aap.memberships[("groups", 200, "hosts")] == {101}
