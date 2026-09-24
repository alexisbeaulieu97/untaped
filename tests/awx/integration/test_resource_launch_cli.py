"""End-to-end CLI tests for AWX launch and action flows."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def _flag_in_help(flag: str, help_text: str) -> bool:
    """True iff ``flag`` appears as a complete flag, not as a longer flag prefix."""
    return re.search(rf"{re.escape(flag)}\b", help_text) is not None


@pytest.mark.parametrize(
    ("args", "input", "launched"),
    [
        (["--stdin"], "alpha\nbeta\n", [10, 11]),
        # all-digit identifiers are names unless --by-id is passed
        (["123", "--org", "Default"], None, [99]),
        (["--by-id", "123"], None, [123]),
        (["shared", "--org", "Other"], None, [51]),
    ],
)
def test_launch_selection(
    seeded_default_org: Any, args: list[str], input: str | None, launched: list[int]
) -> None:
    fake = seeded_default_org
    fake.seed("organizations", id=2, name="Other")
    for id_, name, org in ((10, "alpha", 1), (11, "beta", 1), (99, "123", 1), (123, "other", 1),
                           (50, "shared", 1), (51, "shared", 2)):  # fmt: skip
        fake.seed("job_templates", id=id_, name=name, organization=org)
    result = CliInvoker().invoke(app, ["job-templates", "launch", "--yes", *args], input=input)
    assert result.exit_code == 0, result.output
    assert sorted(c[1] for c in fake.actions_called if c[2] == "launch") == launched


@pytest.mark.parametrize(
    ("args", "action"),
    [
        (["job-templates", "launch", "alpha"], ("job_templates", "launch")),
        (["projects", "sync", "playbooks"], ("projects", "update")),
        (["jobs", "wait", "42"], None),
    ],
)
def test_actions_honour_format_json(
    seeded_default_org: Any, args: list[str], action: tuple[str, str] | None
) -> None:
    """CI scripts pipe launch, sync and wait verdicts into ``jq``."""
    seeded_default_org.seed("job_templates", id=10, name="alpha", organization=1)
    seeded_default_org.seed("projects", id=10, name="playbooks", organization=1, scm_type="git")
    seeded_default_org.seed("jobs", id=42, name="run", status="successful", type="job")
    result = CliInvoker().invoke(app, [*args, "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)
    if action is not None:
        assert [c[0::2] for c in seeded_default_org.actions_called] == [action]


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
            "--host-pattern",
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
    assert json.loads(body["extra_vars"]) == {"foo": 1}
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
        "job_templates",
        id=10,
        name="alpha",
        organization=1,
        organization_name="Default",
        ask_verbosity_on_launch=True,
        ask_diff_mode_on_launch=True,
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
        "--host-pattern",
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


def _launch_body(fake: Any, *args: str) -> dict[str, Any]:
    result = CliInvoker().invoke(app, ["job-templates", "launch", "alpha", *args])
    assert result.exit_code == 0, result.output
    launches = [c for c in fake.actions_called if c[2] == "launch"]
    assert len(launches) == 1
    return launches[0][3]  # type: ignore[no-any-return]


def test_launch_extra_vars_key_values_become_a_json_mapping(seeded_default_org: Any) -> None:
    """Repeated KEY=VAL entries merge into one mapping; JSON values are decoded."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, ask_variables_on_launch=True
    )
    body = _launch_body(
        seeded_default_org,
        "--extra-vars",
        "count=2",
        "--extra-vars",
        "region=us-east",
        "--extra-vars",
        'tags=["a", "b"]',
        "--extra-vars",
        "version=1.10.0",
    )
    assert json.loads(body["extra_vars"]) == {
        "count": 2,
        "region": "us-east",
        "tags": ["a", "b"],
        "version": "1.10.0",
    }


def test_launch_extra_vars_accepts_files_and_raw_mappings(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, ask_variables_on_launch=True
    )
    yml = tmp_path / "vars.yml"
    yml.write_text("region: eu\nnested:\n  enabled: true\n")
    js = tmp_path / "more.json"
    js.write_text('{"count": 3}')
    body = _launch_body(
        seeded_default_org,
        "--extra-vars",
        f"@{yml}",
        "--extra-vars",
        f"@{js}",
        "--extra-vars",
        '{"region": "us"}',
        "--extra-vars",
        "flag: yes",
    )
    assert json.loads(body["extra_vars"]) == {
        "region": "us",
        "nested": {"enabled": True},
        "count": 3,
        "flag": True,
    }


def test_launch_extra_vars_key_values_keep_non_integer_scalars_as_strings(
    seeded_default_org: Any,
) -> None:
    """Only true/false/null, integers and JSON objects/arrays are decoded."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, ask_variables_on_launch=True
    )
    args = [
        "version=1.10",
        "exp=1e3",
        "nan=NaN",
        "inf=Infinity",
        "ratio=1.5",
        "on=true",
        "none=null",
        "n=-7",
        'obj={"a": 1}',
    ]
    body = _launch_body(seeded_default_org, *[x for a in args for x in ("--extra-vars", a)])
    assert json.loads(body["extra_vars"]) == {
        "version": "1.10",
        "exp": "1e3",
        "nan": "NaN",
        "inf": "Infinity",
        "ratio": "1.5",
        "on": True,
        "none": None,
        "n": -7,
        "obj": {"a": 1},
    }


def test_launch_extra_vars_yaml_dates_become_iso_strings(
    seeded_default_org: Any, tmp_path: Path
) -> None:
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, ask_variables_on_launch=True
    )
    yml = tmp_path / "vars.yml"
    yml.write_text("release_date: 2024-01-01\nwhen:\n  - 2024-01-02 03:04:05\n")
    body = _launch_body(
        seeded_default_org, "--extra-vars", f"@{yml}", "--extra-vars", "d: 2024-02-03"
    )
    assert json.loads(body["extra_vars"]) == {
        "release_date": "2024-01-01",
        "when": ["2024-01-02T03:04:05"],
        "d": "2024-02-03",
    }


@pytest.mark.parametrize("entry", ["x: .nan", "x: !!binary aGk=", "[1, 2]"])
def test_launch_extra_vars_rejects_unencodable_values(seeded_default_org: Any, entry: str) -> None:
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, ask_variables_on_launch=True
    )
    result = CliInvoker().invoke(app, ["job-templates", "launch", "alpha", "--extra-vars", entry])
    assert result.exit_code == 2, result.output
    assert "--extra-vars" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert seeded_default_org.actions_called == []


def test_launch_rejects_flags_the_template_does_not_prompt_for(seeded_default_org: Any) -> None:
    """AWX silently drops unprompted fields; refuse before any POST instead."""
    seeded_default_org.seed(
        "job_templates",
        id=10,
        name="alpha",
        organization=1,
        organization_name="Default",
        ask_limit_on_launch=False,
    )
    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "alpha", "--host-pattern", "web1"]
    )
    assert result.exit_code == 2, result.output
    assert "--host-pattern" in result.output
    assert "alpha" in result.output
    assert "ask_limit_on_launch" in result.output
    assert seeded_default_org.actions_called == []


def test_launch_reports_missing_required_survey_variables(seeded_default_org: Any) -> None:
    seeded_default_org.seed(
        "job_templates",
        id=10,
        name="alpha",
        organization=1,
        organization_name="Default",
        survey_enabled=True,
        variables_needed_to_start=["region", "size"],
    )
    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "alpha", "--extra-vars", "region=eu"]
    )
    assert result.exit_code == 2, result.output
    assert "size" in result.output
    assert seeded_default_org.actions_called == []


def test_launch_ignored_fields_fail_the_row(seeded_default_org: Any) -> None:
    """A launch whose response lists ignored_fields did not run as requested."""
    seeded_default_org.seed(
        "job_templates",
        id=10,
        name="alpha",
        organization=1,
        organization_name="Default",
        ask_limit_on_launch=True,
    )
    seeded_default_org.next_action_ignored_fields = {"limit": "web1"}
    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "alpha", "--host-pattern", "web1", "--format", "json"]
    )
    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["action"] == "failed"
    assert "limit" in rows[0]["detail"]
    assert rows[0]["id"] is not None


def _unprompted_alpha(fake: Any) -> None:
    fake.seed("credentials", id=30, name="ssh", organization=1, organization_name="Default")
    fake.seed("credentials", id=31, name="vault", organization=1, organization_name="Default")
    fake.seed("credentials", id=32, name="other", organization=1, organization_name="Default")
    fake.seed(
        "job_templates",
        id=10,
        name="alpha",
        organization=1,
        organization_name="Default",
        limit="web",
        verbosity=1,
        summary_fields={"credentials": [{"id": 30, "name": "ssh"}, {"id": 31, "name": "vault"}]},
    )


@pytest.mark.parametrize(
    "args",
    [
        ["--host-pattern", "web"],
        ["--verbosity", "1"],
        ["--credential", "ssh"],
        ["--credential", "ssh", "--credential", "vault"],
        ["--extra-vars", "{}"],
    ],
)
def test_launch_allows_unprompted_values_equal_to_the_template(
    seeded_default_org: Any, args: list[str]
) -> None:
    """AWX treats a value equal to the template's own as a no-op, not ignored."""
    _unprompted_alpha(seeded_default_org)
    result = CliInvoker().invoke(app, ["job-templates", "launch", "alpha", *args])
    assert result.exit_code == 0, result.output
    assert len(seeded_default_org.actions_called) == 1


@pytest.mark.parametrize(
    ("args", "flag"),
    [
        (["--host-pattern", "db"], "--host-pattern"),
        (["--credential", "ssh", "--credential", "other"], "--credential"),
    ],
)
def test_launch_rejects_unprompted_values_that_differ(
    seeded_default_org: Any, args: list[str], flag: str
) -> None:
    _unprompted_alpha(seeded_default_org)
    result = CliInvoker().invoke(app, ["job-templates", "launch", "alpha", *args])
    assert result.exit_code == 2, result.output
    assert flag in result.output
    assert seeded_default_org.actions_called == []


def _survey_alpha(fake: Any) -> None:
    fake.seed(
        "job_templates",
        id=10,
        name="alpha",
        organization=1,
        organization_name="Default",
        survey_enabled=True,
        survey_spec={"name": "", "description": "", "spec": [{"variable": "region"}]},
    )


def test_launch_survey_accepts_survey_variables(seeded_default_org: Any) -> None:
    _survey_alpha(seeded_default_org)
    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "alpha", "--extra-vars", "region=eu"]
    )
    assert result.exit_code == 0, result.output
    assert len(seeded_default_org.actions_called) == 1


def test_launch_survey_rejects_variables_outside_the_survey(seeded_default_org: Any) -> None:
    """Without ask_variables_on_launch AWX drops extra vars the survey lacks."""
    _survey_alpha(seeded_default_org)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "launch",
            "alpha",
            "--extra-vars",
            "region=eu",
            "--extra-vars",
            "debug=1",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "debug" in result.output
    assert seeded_default_org.actions_called == []


def test_launch_pipe_output_feeds_jobs_stdin(seeded_default_org: Any) -> None:
    """`launch --format pipe | jobs get --stdin` reads the launched execution ids."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    launched = CliInvoker().invoke(app, ["job-templates", "launch", "alpha", "--format", "pipe"])
    assert launched.exit_code == 0, launched.output
    assert json.loads(launched.stdout)["kind"] == "awx.launch_outcome"

    result = CliInvoker().invoke(
        app,
        ["jobs", "get", "--stdin", "--format", "raw", "--columns", "id"],
        input=launched.stdout,
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(json.loads(launched.stdout)["record"]["id"])
