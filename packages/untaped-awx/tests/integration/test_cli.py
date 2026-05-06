"""End-to-end CLI tests against the fake AAP fixture.

``FakeAap`` is provided by the ``fake_aap`` fixture in
``tests/conftest.py``; we use ``Any`` for type annotations to dodge
the importlib-mode cross-file import problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from untaped_awx import app

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


def test_job_templates_list(fake_aap: Any) -> None:
    _seed_basic(fake_aap)
    result = CliRunner().invoke(
        app,
        ["job-templates", "list", "--format", "raw", "--columns", "name"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy"


def test_list_with_names_flips_fk_ids_to_names(fake_aap: Any) -> None:
    """``--with-names`` swaps FK columns from numeric ids to the names
    AWX returns under ``summary_fields``. Without the flag, the column
    holds the raw id (the FK-piping shape)."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    fake_aap.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    fake_aap.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        project=10,
        project_name="playbooks",
        inventory=20,
        inventory_name="prod",
        playbook="a.yml",
        summary_fields={
            "organization": {"id": 1, "name": "Default"},
            "project": {"id": 10, "name": "playbooks"},
            "inventory": {"id": 20, "name": "prod"},
        },
    )
    raw_default = CliRunner().invoke(
        app,
        [
            "job-templates",
            "list",
            "--format",
            "raw",
            "--columns",
            "project",
            "--columns",
            "inventory",
        ],
    )
    assert raw_default.exit_code == 0, raw_default.output
    assert raw_default.stdout.strip() == "10\t20"

    raw_named = CliRunner().invoke(
        app,
        [
            "job-templates",
            "list",
            "--with-names",
            "--format",
            "raw",
            "--columns",
            "project",
            "--columns",
            "inventory",
        ],
    )
    assert raw_named.exit_code == 0, raw_named.output
    assert raw_named.stdout.strip() == "playbooks\tprod"


def test_list_with_names_handles_multi_fk(fake_aap: Any) -> None:
    """Multi-valued FKs (credentials) become a list of names."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("credentials", id=30, name="ssh", organization=1, organization_name="Default")
    fake_aap.seed("credentials", id=31, name="vault", organization=1, organization_name="Default")
    fake_aap.seed(
        "job_templates",
        id=10,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
        credentials=[30, 31],
        summary_fields={
            "organization": {"id": 1, "name": "Default"},
            "credentials": [
                {"id": 30, "name": "ssh"},
                {"id": 31, "name": "vault"},
            ],
        },
    )
    result = CliRunner().invoke(
        app,
        [
            "job-templates",
            "list",
            "--with-names",
            "--format",
            "raw",
            "--columns",
            "credentials",
        ],
    )
    assert result.exit_code == 0, result.output
    # Scalar lists render comma-separated for raw/table.
    assert result.stdout.strip() == "ssh, vault"


def test_list_with_names_falls_back_to_id_when_summary_missing(fake_aap: Any) -> None:
    """If summary_fields is absent (degraded server response), the row
    keeps the raw id rather than disappearing or rendering empty."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "job_templates",
        id=10,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
        # No summary_fields seeded.
    )
    result = CliRunner().invoke(
        app,
        [
            "job-templates",
            "list",
            "--with-names",
            "--format",
            "raw",
            "--columns",
            "organization",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "1"


def test_list_dotted_columns_resolve_summary_fields(fake_aap: Any) -> None:
    """``--columns summary_fields.project.name`` works without --with-names —
    the dotted accessor traverses nested dicts in the row."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "job_templates",
        id=10,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
        project=20,
        summary_fields={"project": {"id": 20, "name": "playbooks"}},
    )
    result = CliRunner().invoke(
        app,
        [
            "job-templates",
            "list",
            "--format",
            "raw",
            "--columns",
            "name",
            "--columns",
            "summary_fields.project.name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy\tplaybooks"


def test_get_format_table_defaults_to_list_columns(fake_aap: Any) -> None:
    """``get --format table`` without ``--columns`` must project to the
    spec's list_columns. Rendering the full AWX record (50+ fields with
    nested dicts stringified) is unreadable noise."""
    _seed_basic(fake_aap)
    result = CliRunner().invoke(
        app,
        ["job-templates", "get", "deploy", "--organization", "Default", "--format", "table"],
    )
    assert result.exit_code == 0, result.output
    # list_columns for JT is ("name", "organization", "project", "inventory", "last_job_status").
    # No noisy columns like "summary_fields" or "related" should appear.
    assert "summary_fields" not in result.stdout
    assert "related" not in result.stdout
    assert "deploy" in result.stdout


def test_get_format_raw_keeps_first_key_default(fake_aap: Any) -> None:
    """``get --format raw`` without ``--columns`` must keep
    ``format_output``'s first-key behavior so pipelines like
    ``get --stdin --format raw | …`` retain their established shape."""
    _seed_basic(fake_aap)
    result = CliRunner().invoke(
        app,
        ["job-templates", "get", "deploy", "--organization", "Default", "--format", "raw"],
    )
    assert result.exit_code == 0, result.output
    # Single line, single column — not a tab-separated multi-column wall.
    assert "\t" not in result.stdout.strip()
    assert "\n" not in result.stdout.strip()


def test_get_with_names_translates_fks(fake_aap: Any) -> None:
    """``get --with-names`` works the same way as on list."""
    _seed_basic(fake_aap)
    # Inject summary_fields so the translation has data to read.
    fake_aap.get_record("job_templates", 30)["summary_fields"] = {
        "organization": {"id": 1, "name": "Default"},
        "project": {"id": 10, "name": "playbooks"},
        "inventory": {"id": 20, "name": "prod"},
    }
    result = CliRunner().invoke(
        app,
        [
            "job-templates",
            "get",
            "deploy",
            "--organization",
            "Default",
            "--with-names",
            "--format",
            "raw",
            "--columns",
            "project",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "playbooks"


def test_job_templates_get(fake_aap: Any) -> None:
    _seed_basic(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "job-templates",
            "get",
            "deploy",
            "--organization",
            "Default",
            "--format",
            "raw",
            "--columns",
            "playbook",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy.yml"


def test_job_templates_save_translates_fks(fake_aap: Any, tmp_path: Path) -> None:
    _seed_basic(fake_aap)
    out = tmp_path / "jt.yml"
    result = CliRunner().invoke(
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
    result = CliRunner().invoke(app, ["job-templates", "apply", "--file", str(f)])
    assert result.exit_code == 0, result.output
    # State on the server is unchanged because we didn't pass --yes.
    jt = fake_aap.get_record("job_templates", 30)
    assert jt["description"] == "deploy the app"


def test_get_accepts_multiple_positional_names(fake_aap: Any) -> None:
    """Identifier-taking commands must support repeated positionals so users
    can fetch several resources in one call (then pipe to format_output)."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("job_templates", id=10, name="alpha", organization=1, organization_name="Default")
    fake_aap.seed("job_templates", id=11, name="beta", organization=1, organization_name="Default")
    result = CliRunner().invoke(
        app, ["job-templates", "get", "alpha", "beta", "--format", "raw", "--columns", "name"]
    )
    assert result.exit_code == 0, result.output
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_get_reads_names_from_stdin(fake_aap: Any) -> None:
    """`list ... | get --stdin` is the documented pipeline shape per
    AGENTS.md "Output & Piping Conventions"."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("job_templates", id=10, name="alpha", organization=1, organization_name="Default")
    fake_aap.seed("job_templates", id=11, name="beta", organization=1, organization_name="Default")
    result = CliRunner().invoke(
        app,
        ["job-templates", "get", "--stdin", "--format", "raw", "--columns", "name"],
        input="alpha\nbeta\n",
    )
    assert result.exit_code == 0, result.output
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_get_accepts_numeric_id_positional(fake_aap: Any) -> None:
    """Numeric identifiers must be looked up by id, not by name.

    Lets users pipe FK columns straight into another resource's `get`:
    `job-templates list --columns project --format raw | projects get --stdin`.
    """
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliRunner().invoke(
        app, ["projects", "get", "10", "--format", "raw", "--columns", "name"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "playbooks"


def test_get_by_id_ignores_organization_scope(fake_aap: Any) -> None:
    """Numeric ids are globally unique, so the org scope must not be applied
    (otherwise looking up by id requires the user to know the org, which
    defeats the purpose of having an id)."""
    fake_aap.seed("organizations", id=1, name="Org-A")
    fake_aap.seed("organizations", id=2, name="Org-B")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=2,
        organization_name="Org-B",
        scm_type="git",
    )
    result = CliRunner().invoke(
        app,
        [
            "projects",
            "get",
            "10",
            "--organization",
            "Org-A",  # wrong org, must be ignored
            "--format",
            "raw",
            "--columns",
            "name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "playbooks"


def test_get_reads_ids_from_stdin(fake_aap: Any) -> None:
    """Pipeline shape: `job-templates list --columns project --format raw |
    projects get --stdin` must look each entry up by id."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    fake_aap.seed(
        "projects",
        id=11,
        name="ops",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliRunner().invoke(
        app,
        ["projects", "get", "--stdin", "--format", "raw", "--columns", "name"],
        input="10\n11\n",
    )
    assert result.exit_code == 0, result.output
    assert "playbooks" in result.stdout
    assert "ops" in result.stdout


def test_get_mixes_names_and_ids(fake_aap: Any) -> None:
    """A single batch can mix names and numeric ids — name entries still
    honour the resolved organization scope."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    fake_aap.seed(
        "projects",
        id=11,
        name="ops",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliRunner().invoke(
        app,
        [
            "projects",
            "get",
            "playbooks",
            "11",
            "--organization",
            "Default",
            "--format",
            "raw",
            "--columns",
            "name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "playbooks" in result.stdout
    assert "ops" in result.stdout


def test_get_by_missing_id_reports_error(fake_aap: Any) -> None:
    """A missing numeric id must surface as a per-item error and a
    non-zero exit, just like a missing name does."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliRunner().invoke(
        app,
        ["projects", "get", "--stdin", "--format", "raw", "--columns", "name"],
        input="10\n9999\n",
    )
    assert result.exit_code != 0
    # Successful lookup still reaches stdout.
    assert "playbooks" in result.stdout
    # The missing id surfaces on stderr.
    assert "9999" in (result.output + (result.stderr or ""))


def test_get_rejects_mixed_positional_and_stdin(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("job_templates", id=10, name="alpha", organization=1, organization_name="Default")
    result = CliRunner().invoke(app, ["job-templates", "get", "alpha", "--stdin"], input="beta\n")
    assert result.exit_code != 0
    # Confirm the failure is the intentional mutually-exclusive rejection,
    # not a crash bubbling up an unrelated exception.
    assert "stdin" in (result.output + (result.stderr or "")).lower()


def test_launch_reads_names_from_stdin(fake_aap: Any) -> None:
    """`launch --stdin` fans out launches across every identifier read from
    stdin — same pipeline shape as `get --stdin`."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("job_templates", id=10, name="alpha", organization=1, organization_name="Default")
    fake_aap.seed("job_templates", id=11, name="beta", organization=1, organization_name="Default")
    result = CliRunner().invoke(app, ["job-templates", "launch", "--stdin"], input="alpha\nbeta\n")
    assert result.exit_code == 0, result.output
    launches = [c for c in fake_aap.actions_called if c[2] == "launch"]
    launched_ids = {c[1] for c in launches}
    assert launched_ids == {10, 11}


def test_get_without_scope_raises_when_name_is_ambiguous(fake_aap: Any) -> None:
    """A name that exists in multiple orgs must raise (no silent first-match)."""
    fake_aap.seed("organizations", id=1, name="Org-A")
    fake_aap.seed("organizations", id=2, name="Org-B")
    fake_aap.seed("job_templates", id=10, name="deploy", organization=1, organization_name="Org-A")
    fake_aap.seed("job_templates", id=11, name="deploy", organization=2, organization_name="Org-B")

    result = CliRunner().invoke(app, ["job-templates", "get", "deploy"])
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "ambiguous" in output.lower(), output


def test_get_with_scope_resolves_unambiguously(fake_aap: Any) -> None:
    """Adding the missing scope removes the ambiguity."""
    fake_aap.seed("organizations", id=1, name="Org-A")
    fake_aap.seed("organizations", id=2, name="Org-B")
    fake_aap.seed("job_templates", id=10, name="deploy", organization=1, organization_name="Org-A")
    fake_aap.seed("job_templates", id=11, name="deploy", organization=2, organization_name="Org-B")

    result = CliRunner().invoke(
        app,
        ["job-templates", "get", "deploy", "--organization", "Org-A", "--format", "json"],
    )
    assert result.exit_code == 0, result.output


def test_launch_supports_format_json(fake_aap: Any) -> None:
    """The pipeline contract: launch must honour --format/--columns
    instead of forcing yaml output."""
    import json as _json

    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("job_templates", id=10, name="alpha", organization=1, organization_name="Default")
    result = CliRunner().invoke(app, ["job-templates", "launch", "alpha", "--format", "json"])
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed, parsed


def test_workflow_launch_rejects_unsupported_flags(fake_aap: Any) -> None:
    """Workflow templates accept a subset of JobTemplate's launch flags.
    Passing an unsupported one (here: --verbosity, --diff-mode,
    --credential, --job-type) must fail with a clear error rather than
    silently dropping the value."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "workflow_job_templates", id=10, name="wf", organization=1, organization_name="Default"
    )

    result = CliRunner().invoke(
        app,
        [
            "workflow-templates",
            "launch",
            "wf",
            "--organization",
            "Default",
            "--verbosity",
            "3",
        ],
    )
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "--verbosity" in output
    assert "WorkflowJobTemplate.launch does not accept" in output


def test_launch_forwards_full_action_payload(fake_aap: Any) -> None:
    """Every flag listed in JobTemplate.launch.accepts must reach the
    POST body, with FK names (--inventory, --credential) resolved via
    the FkResolver and list flags (--job-tag/--skip-tag/--credential)
    accumulated correctly."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    fake_aap.seed(
        "credentials",
        id=30,
        name="ssh",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "credentials",
        id=31,
        name="vault",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed("job_templates", id=10, name="alpha", organization=1, organization_name="Default")

    result = CliRunner().invoke(
        app,
        [
            "job-templates",
            "launch",
            "alpha",
            "--organization",
            "Default",
            "--extra-vars",
            "foo=1",
            "--limit",
            "web*",
            "--inventory",
            "prod",
            "--credential",
            "ssh",
            "--credential",
            "vault",
            "--scm-branch",
            "release",
            "--job-tag",
            "deploy",
            "--job-tag",
            "smoke",
            "--skip-tag",
            "slow",
            "--verbosity",
            "3",
            "--diff-mode",
            "--job-type",
            "check",
        ],
    )
    assert result.exit_code == 0, result.output

    launches = [c for c in fake_aap.actions_called if c[2] == "launch"]
    assert len(launches) == 1
    body = launches[0][3]
    assert body["extra_vars"] == "foo=1"
    assert body["limit"] == "web*"
    assert body["inventory"] == 20
    assert body["credentials"] == [30, 31]
    assert body["scm_branch"] == "release"
    assert body["job_tags"] == "deploy,smoke"
    assert body["skip_tags"] == "slow"
    assert body["verbosity"] == 3
    assert body["diff_mode"] is True
    assert body["job_type"] == "check"


def test_jobs_wait_supports_format_json(fake_aap: Any) -> None:
    """`awx jobs wait` must honour --format — CI scripts that pipe a
    wait verdict into ``jq`` rely on the structured shape."""
    import json as _json

    fake_aap.seed("jobs", id=42, name="run", status="successful", type="job")
    result = CliRunner().invoke(app, ["jobs", "wait", "42", "--format", "json"])
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed
    assert parsed[0].get("id") == 42


def test_jobs_wait_exits_nonzero_on_timeout(fake_aap: Any) -> None:
    """A non-terminal job at the deadline must exit non-zero — `awx test`
    already classifies that as ``timeout``; `jobs wait` should agree so
    scripts can ``set -e`` and detect the failure."""
    fake_aap.seed("jobs", id=42, name="run", status="running", type="job")
    result = CliRunner().invoke(app, ["jobs", "wait", "42", "--timeout", "0"])
    assert result.exit_code == 1, result.output
    assert "timeout" in (result.output + (result.stderr or ""))


def test_project_update_supports_format_json(fake_aap: Any) -> None:
    """The generated `<kind> update` command on Project must honour
    --format too. Symmetric with launch."""
    import json as _json

    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliRunner().invoke(
        app, ["projects", "update", "playbooks", "--organization", "Default", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed


def test_launch_stdin_emits_partial_results_when_one_fails(fake_aap: Any) -> None:
    """A missing name mid-fan-out must not hide the IDs of the jobs that
    already submitted to AWX. Otherwise a user piping 50 names sees only
    the error for the first failure and has no record of the running jobs.
    """
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("job_templates", id=10, name="alpha", organization=1, organization_name="Default")
    # No "ghost" template — second call will fail.
    result = CliRunner().invoke(app, ["job-templates", "launch", "--stdin"], input="alpha\nghost\n")
    # Non-zero exit because ghost failed.
    assert result.exit_code != 0
    # alpha did launch — its action call is recorded server-side.
    launches = [c for c in fake_aap.actions_called if c[2] == "launch"]
    assert any(c[1] == 10 for c in launches)
    # alpha's job dict must reach stdout — without per-item resilience,
    # the format_output call after the loop never runs and the user
    # has no record of the running job.
    assert result.stdout.strip(), "expected partial-success stdout, got empty"
    # ghost's error must surface on stderr.
    assert "ghost" in (result.output + (result.stderr or ""))


def test_get_stdin_continues_on_missing_name(fake_aap: Any) -> None:
    """A missing name in a multi-name `get --stdin` batch must not
    suppress the names that resolved successfully."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("job_templates", id=10, name="alpha", organization=1, organization_name="Default")
    result = CliRunner().invoke(
        app,
        ["job-templates", "get", "--stdin", "--format", "raw", "--columns", "name"],
        input="alpha\nghost\n",
    )
    assert result.exit_code != 0
    # alpha's row reaches stdout even though ghost failed.
    assert "alpha" in result.stdout
    assert "ghost" in (result.output + (result.stderr or ""))


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
    result = CliRunner().invoke(app, ["job-templates", "apply", "--file", str(f), "--yes"])
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
    result = CliRunner().invoke(app, ["job-templates", "apply", "--file", str(f), "--yes"])
    assert result.exit_code == 0, result.output
    jt = fake_aap.get_record("job_templates", 30)
    assert jt["description"] == "changed-via-apply"


def test_save_all_rejects_traversal_in_resource_names(fake_aap: Any, tmp_path: Path) -> None:
    """Resource names with `/` or `..` must not produce dangerous filesystem paths."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects",
        id=10,
        name="evil/../escape",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    out_dir = tmp_path / "backup"
    result = CliRunner().invoke(app, ["save", "--all", "--out-dir", str(out_dir)])
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


def test_jobs_logs_returns_text_not_json(fake_aap: Any) -> None:
    """`jobs logs` hits a text endpoint — must not JSON-decode."""
    fake_aap.seed(
        "jobs",
        id=42,
        name="deploy-1",
        status="successful",
        stdout="PLAY [deploy] **\nTASK [run] **\nok: [host1]\n",
    )
    result = CliRunner().invoke(app, ["jobs", "logs", "42"])
    assert result.exit_code == 0, result.output
    assert "PLAY [deploy]" in result.stdout
    assert "TASK [run]" in result.stdout


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
    result = CliRunner().invoke(app, ["job-templates", "apply", "--file", str(f), "--yes"])
    assert result.exit_code == 0, result.output
    # JT got patched
    jt = fake_aap.get_record("job_templates", 30)
    assert jt["playbook"] == "changed.yml"
    # Project untouched — no scm_type=hg leaked through
    project = fake_aap.get_record("projects", 10)
    assert project["scm_type"] == original_project["scm_type"] == "git"
    # Wrong-kind warning visible
    assert "Project" in result.stderr


def test_apply_creates_when_missing(fake_aap: Any, tmp_path: Path) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    f = tmp_path / "p.yml"
    f.write_text(
        "kind: Project\n"
        "metadata: { name: new-proj, organization: Default }\n"
        "spec:\n"
        "  scm_type: git\n"
        "  scm_url: https://example.com/x.git\n"
    )
    result = CliRunner().invoke(app, ["projects", "apply", "--file", str(f), "--yes"])
    assert result.exit_code == 0, result.output
    new_proj = next(r for r in fake_aap.list_records("projects") if r["name"] == "new-proj")
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
    result = CliRunner().invoke(app, ["job-templates", "apply", "--file", str(f), "--yes"])
    assert result.exit_code == 0, result.output
    jt = fake_aap.get_record("job_templates", 30)
    assert jt["webhook_key"] == "$encrypted$"  # untouched
    assert jt["description"] == "still-deploy"


def test_credentials_have_no_save_or_apply(fake_aap: Any) -> None:
    """Credential is read-only — its sub-app should not expose save/apply."""
    result = CliRunner().invoke(app, ["credentials", "save", "x"])
    assert result.exit_code != 0
    assert "no such command" in result.output.lower() or "usage" in result.output.lower()


def test_save_all_filter_scopes_org_kinds_server_side(fake_aap: Any, tmp_path: Path) -> None:
    """`save --all --filter organization__name=X` is passed verbatim to AWX
    for every saved kind, so org-scoped kinds (JT, Project) get filtered
    server-side and other-org records don't leak through."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("organizations", id=2, name="Other")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    fake_aap.seed(
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
    fake_aap.seed(
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
    result = CliRunner().invoke(
        app,
        [
            "save",
            "--all",
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
    fake_aap: Any, tmp_path: Path
) -> None:
    """Schedule's API has no ``organization`` field, so AWX would 400 on
    ``?organization__name=…``. Bulk save must detect that the filter
    references a field this kind doesn't have, skip the kind with a
    stderr warning, and continue with the kinds that do support it."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    fake_aap.seed(
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
    result = CliRunner().invoke(
        app,
        [
            "save",
            "--all",
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


def test_save_kind_accepts_cli_name(fake_aap: Any, tmp_path: Path) -> None:
    """``save --kind job-templates`` should work as well as ``--kind JobTemplate``."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    out_dir = tmp_path / "backup"
    result = CliRunner().invoke(app, ["save", "--out-dir", str(out_dir), "--kind", "job-templates"])
    assert result.exit_code == 0, result.output
    assert (out_dir / "JobTemplate__Default__deploy.yml").exists()


def test_save_all_with_no_filter_captures_every_kind(
    fake_aap: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bulk save with no ``--filter`` is "back up everything": JTs across
    every org plus parent-scoped kinds (Schedule). ``default_organization``
    must not silently narrow the backup — it's a name-disambiguation hint
    for ``get``/``launch``/``update``, not a save scope."""
    monkeypatch.setenv("UNTAPED_AWX__DEFAULT_ORGANIZATION", "Default")
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
    result = CliRunner().invoke(app, ["save", "--all", "--out-dir", str(out_dir)])
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
    result = CliRunner().invoke(
        app, ["save", "--all", "--out-dir", str(out_dir), "--filter", "bogus"]
    )
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "KEY=VALUE" in output


def test_save_all_distinguishes_same_named_resources_across_orgs(
    fake_aap: Any, tmp_path: Path
) -> None:
    """Two same-named org-scoped resources in different orgs must produce two distinct files."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("organizations", id=2, name="Other")
    fake_aap.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
    )
    fake_aap.seed(
        "job_templates",
        id=31,
        name="deploy",  # same name, different org
        organization=2,
        organization_name="Other",
        playbook="b.yml",
    )

    out_dir = tmp_path / "backup"
    result = CliRunner().invoke(app, ["save", "--all", "--out-dir", str(out_dir)])
    assert result.exit_code == 0, result.output

    saved = sorted(p.name for p in out_dir.glob("JobTemplate__*.yml"))
    assert saved == [
        "JobTemplate__Default__deploy.yml",
        "JobTemplate__Other__deploy.yml",
    ], f"expected two distinct files for same-named JTs in different orgs, got {saved}"


def test_save_all_skips_credentials(fake_aap: Any, tmp_path: Path) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    fake_aap.seed(
        "credentials",
        id=20,
        name="ssh-key",
        organization=1,
        organization_name="Default",
        credential_type=1,
    )
    out_dir = tmp_path / "backup"
    result = CliRunner().invoke(
        app,
        ["save", "--all", "--out-dir", str(out_dir)],
    )
    assert result.exit_code == 0, result.output
    # Project file exists; Credential file does not.
    assert (out_dir / "Project__Default__playbooks.yml").exists()
    assert not any(p.name.startswith("Credential__") for p in out_dir.iterdir())
    assert "skipping Credential" in result.stderr


def test_workflow_save_emits_partial_warning(fake_aap: Any, tmp_path: Path) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "workflow_job_templates",
        id=10,
        name="pipeline",
        organization=1,
        organization_name="Default",
        description="multi-step",
    )
    out = tmp_path / "wf.yml"
    result = CliRunner().invoke(
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


def test_project_update_calls_action(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliRunner().invoke(
        app,
        ["projects", "update", "playbooks", "--organization", "Default"],
    )
    assert result.exit_code == 0, result.output
    assert any(
        api_path == "projects" and action == "update"
        for api_path, _, action, _ in fake_aap.actions_called
    )
