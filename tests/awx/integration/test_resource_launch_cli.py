"""End-to-end CLI tests for AWX launch and action flows."""

from __future__ import annotations

import re
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def _flag_in_help(flag: str, help_text: str) -> bool:
    """True iff ``flag`` appears as a complete flag, not as a longer flag prefix."""
    return re.search(rf"{re.escape(flag)}\b", help_text) is not None


def test_launch_reads_names_from_stdin(seeded_default_org: Any) -> None:
    """`launch --stdin` fans out launches across every identifier read from
    stdin — same pipeline shape as `get --stdin`."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "job_templates", id=11, name="beta", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(app, ["job-templates", "launch", "--stdin"], input="alpha\nbeta\n")
    assert result.exit_code == 0, result.output
    launches = [c for c in seeded_default_org.actions_called if c[2] == "launch"]
    launched_ids = {c[1] for c in launches}
    assert launched_ids == {10, 11}


def test_launch_numeric_name_is_default(seeded_default_org: Any) -> None:
    """All-digit launch identifiers are template names unless ``--by-id`` is passed."""
    seeded_default_org.seed(
        "job_templates", id=99, name="123", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "job_templates", id=123, name="other", organization=1, organization_name="Default"
    )

    result = CliInvoker().invoke(app, ["job-templates", "launch", "123", "--org", "Default"])

    assert result.exit_code == 0, result.output
    launches = [c for c in seeded_default_org.actions_called if c[2] == "launch"]
    assert len(launches) == 1
    assert launches[0][1] == 99


def test_launch_by_id_uses_awx_id(seeded_default_org: Any) -> None:
    seeded_default_org.seed(
        "job_templates", id=99, name="123", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "job_templates", id=123, name="other", organization=1, organization_name="Default"
    )

    result = CliInvoker().invoke(app, ["job-templates", "launch", "--by-id", "123"])

    assert result.exit_code == 0, result.output
    launches = [c for c in seeded_default_org.actions_called if c[2] == "launch"]
    assert len(launches) == 1
    assert launches[0][1] == 123


def test_launch_supports_format_json(seeded_default_org: Any) -> None:
    """The pipeline contract: launch must honour --format/--columns
    instead of forcing yaml output."""
    import json as _json

    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(app, ["job-templates", "launch", "alpha", "--format", "json"])
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed, parsed


def test_launch_accepts_org_alias_for_name_scope(fake_aap: Any) -> None:
    fake_aap.seed("organizations", id=1, name="Org-A")
    fake_aap.seed("organizations", id=2, name="Org-B")
    fake_aap.seed("job_templates", id=10, name="deploy", organization=1, organization_name="Org-A")
    fake_aap.seed("job_templates", id=11, name="deploy", organization=2, organization_name="Org-B")

    result = CliInvoker().invoke(
        app,
        ["job-templates", "launch", "deploy", "--org", "Org-B", "--format", "raw"],
    )

    assert result.exit_code == 0, result.output
    launches = [c for c in fake_aap.actions_called if c[2] == "launch"]
    assert len(launches) == 1
    assert launches[0][1] == 11


def test_workflow_launch_rejects_unsupported_flags(seeded_default_org: Any) -> None:
    """Workflow templates accept a subset of JobTemplate's launch flags.
    Passing an unsupported one (here: --verbosity, --diff-mode,
    --credential, --job-type) must fail with a clear error rather than
    silently dropping the value."""
    seeded_default_org.seed(
        "workflow_job_templates", id=10, name="wf", organization=1, organization_name="Default"
    )

    result = CliInvoker().invoke(
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


def test_launch_forwards_full_action_payload(
    seeded_job_template_with_credentials: Any,
) -> None:
    """Every flag listed in JobTemplate.launch.accepts must reach the
    POST body, with FK names (--inventory, --credential) resolved via
    the FkResolver and list flags (--job-tag/--skip-tag/--credential)
    accumulated correctly."""
    fake_aap, ids = seeded_job_template_with_credentials

    result = CliInvoker().invoke(
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
    assert body["inventory"] == ids["inventory"]
    assert body["credentials"] == [ids["ssh"], ids["vault"]]
    assert body["scm_branch"] == "release"
    assert body["job_tags"] == "deploy,smoke"
    assert body["skip_tags"] == "slow"
    assert body["verbosity"] == 3
    assert body["diff_mode"] is True
    assert body["job_type"] == "check"


def test_launch_round_trips_falsy_but_meaningful_flag_values(
    seeded_default_org: Any,
) -> None:
    """``--verbosity 0`` and ``--no-diff-mode`` carry distinct meaning
    from "flag not supplied" and must reach the AWX POST body. The
    refactor's ``_is_supplied`` predicate is deliberately ``value is
    not None and value != []`` (not ``bool(value)``) for exactly this
    case; a future "simplify" pass that switched to truthy filtering
    would silently drop both values."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "launch",
            "alpha",
            "--verbosity",
            "0",
            "--no-diff-mode",
        ],
    )
    assert result.exit_code == 0, result.output

    launches = [c for c in seeded_default_org.actions_called if c[2] == "launch"]
    assert len(launches) == 1
    body = launches[0][3]
    assert body["verbosity"] == 0
    assert body["diff_mode"] is False


def test_jobs_wait_supports_format_json(fake_aap: Any) -> None:
    """`awx jobs wait` must honour --format — CI scripts that pipe a
    wait verdict into ``jq`` rely on the structured shape."""
    import json as _json

    fake_aap.seed("jobs", id=42, name="run", status="successful", type="job")
    result = CliInvoker().invoke(app, ["jobs", "wait", "42", "--format", "json"])
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed
    assert parsed[0].get("id") == 42


def test_jobs_wait_exits_nonzero_on_timeout(fake_aap: Any) -> None:
    """A non-terminal job at the deadline must exit non-zero — `awx test`
    already classifies that as ``timeout``; `jobs wait` should agree so
    scripts can ``set -e`` and detect the failure."""
    fake_aap.seed("jobs", id=42, name="run", status="running", type="job")
    result = CliInvoker().invoke(app, ["jobs", "wait", "42", "--timeout", "0"])
    assert result.exit_code == 1, result.output
    assert "timeout" in (result.output + (result.stderr or ""))


def test_project_sync_supports_format_json(seeded_default_org: Any) -> None:
    """The generated `<kind> sync` command on Project must honour
    --format too. Symmetric with launch."""
    import json as _json

    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app, ["projects", "sync", "playbooks", "--organization", "Default", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    parsed = _json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed


def test_launch_stdin_preflights_every_name_before_submitting(seeded_default_org: Any) -> None:
    """Known missing targets invalidate the complete selection before any POST."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "--stdin"], input="alpha\nghost\n"
    )
    assert result.exit_code != 0
    assert seeded_default_org.actions_called == []
    assert "ghost" in result.output


def test_jobs_logs_returns_text_not_json(fake_aap: Any) -> None:
    """`jobs logs` hits a text endpoint — must not JSON-decode."""
    fake_aap.seed(
        "jobs",
        id=42,
        name="deploy-1",
        status="successful",
        stdout="PLAY [deploy] **\nTASK [run] **\nok: [host1]\n",
    )
    result = CliInvoker().invoke(app, ["jobs", "logs", "42"])
    assert result.exit_code == 0, result.output
    assert "PLAY [deploy]" in result.stdout
    assert "TASK [run]" in result.stdout


def test_project_sync_calls_action(seeded_default_org: Any) -> None:
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        ["projects", "sync", "playbooks", "--organization", "Default"],
    )
    assert result.exit_code == 0, result.output
    assert any(
        api_path == "projects" and action == "update"
        for api_path, _, action, _ in seeded_default_org.actions_called
    )


def test_launch_help_narrows_flags_by_accepts() -> None:
    """Pins the help-text contract (not the parsing contract): each
    launch flag whose payload field isn't in a kind's ``accepts`` is
    hidden. WJT's ``accepts`` is a strict subset (4 flags hidden); JT's
    is the full set (regression sentinel — every narrowable flag
    advertised).
    """
    runner = CliInvoker()

    wjt_help = runner.invoke(app, ["workflow-templates", "launch", "--help"])
    assert wjt_help.exit_code == 0, wjt_help.output
    # Hidden — payload field not in WJT.launch.accepts.
    for hidden_flag in ("--credential", "--verbosity", "--diff-mode", "--job-type"):
        assert not _flag_in_help(hidden_flag, wjt_help.output), (
            f"{hidden_flag} should be hidden from WJT launch --help"
        )
    # Visible — in accepts (or always-on).
    for visible_flag in (
        "--inventory",
        "--scm-branch",
        "--job-tag",
        "--skip-tag",
        "--extra-vars",
        "--limit",
        "--wait",
        "--track",
    ):
        assert _flag_in_help(visible_flag, wjt_help.output), (
            f"{visible_flag} missing from WJT launch --help"
        )

    jt_help = runner.invoke(app, ["job-templates", "launch", "--help"])
    assert jt_help.exit_code == 0, jt_help.output
    # JobTemplate's accepts contains every narrowable field — full
    # parser stays advertised.
    for narrowable_flag in (
        "--inventory",
        "--credential",
        "--scm-branch",
        "--job-tag",
        "--skip-tag",
        "--verbosity",
        "--diff-mode",
        "--job-type",
    ):
        assert _flag_in_help(narrowable_flag, jt_help.output), (
            f"{narrowable_flag} missing from JT launch --help"
        )
