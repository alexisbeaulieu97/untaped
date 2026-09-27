"""End-to-end CLI tests for ``awx test`` (run, list, validate)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from untaped.capabilities.awx.application import WatchJob
from untaped.capabilities.awx.application.suites.runner import RunTestSuite
from untaped.capabilities.awx.cli import app
from untaped.capabilities.awx.cli.context import AwxContext
from untaped.testing import CliInvoker

if TYPE_CHECKING:  # pragma: no cover — pytest --import-mode=importlib hides 'tests'
    from tests.conftest import FakeAap
else:
    FakeAap = object  # type: ignore[assignment,misc]


@pytest.fixture
def cli() -> CliInvoker:
    return CliInvoker()


@pytest.fixture(autouse=True)
def aap_config_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    yield cfg


def _seed_jt(fake: FakeAap, *, name: str = "Deploy app") -> None:
    fake.seed("job_templates", name=name, ask_variables_on_launch=True, ask_limit_on_launch=True)


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_test_help_lists_subcommands(cli: CliInvoker) -> None:
    result = cli.invoke(app, ["test", "--help"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "run" in out
    assert "list" in out
    assert "validate" in out


def test_run_against_missing_file_emits_clean_error(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """A typo'd test path should not leak a Python traceback."""
    missing = tmp_path / "does-not-exist.yml"
    result = cli.invoke(app, ["test", "run", str(missing), "--non-interactive"])
    assert result.exit_code != 0
    combined = (result.stderr or "") + (result.output or "")
    assert "Traceback" not in combined
    assert "does not exist" in combined


def test_run_with_broken_vars_file_emits_clean_error(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    test_file = _write(
        tmp_path / "needs.yml",
        "---\nvariables:\n  env: { type: string }\n---\n"
        "kind: AwxTestSuite\nname: x\njobTemplate: Deploy app\n"
        "cases:\n  c:\n    launch:\n      limit: '{{ env }}'\n",
    )
    bad_vars = tmp_path / "bad.yml"
    bad_vars.write_text("env: : not yaml\n")  # malformed

    result = cli.invoke(
        app,
        [
            "test",
            "run",
            str(test_file),
            "--vars-file",
            str(bad_vars),
            "--non-interactive",
        ],
    )
    assert result.exit_code != 0
    combined = (result.stderr or "") + (result.output or "")
    assert "Traceback" not in combined


def test_run_passes_when_job_succeeds(cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path) -> None:
    _seed_jt(fake_aap)
    test_file = _write(
        tmp_path / "smoke.yml",
        "kind: AwxTestSuite\n"
        "name: smoke\n"
        "jobTemplate: Deploy app\n"
        "cases:\n"
        "  one:\n"
        "    launch:\n"
        "      limit: web-*\n",
    )

    result = cli.invoke(app, ["test", "run", str(test_file), "--non-interactive"])

    assert result.exit_code == 0, result.stderr or result.output
    assert "pass" in result.stdout
    # FakeAap records the launch action
    assert any(action == "launch" for _, _, action, _ in fake_aap.actions_called)


def test_run_errors_when_awx_ignores_launch_fields(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """A case whose limit AWX ignored ran against the whole inventory: not a pass."""
    fake_aap.seed("job_templates", name="Deploy app")
    test_file = _write(
        tmp_path / "smoke.yml",
        "kind: AwxTestSuite\nname: smoke\njobTemplate: Deploy app\n"
        "cases:\n  one:\n    launch:\n      limit: web-*\n",
    )

    result = cli.invoke(
        app, ["test", "run", str(test_file), "--non-interactive", "--format", "json"]
    )

    assert result.exit_code != 0
    row = json.loads(result.stdout)[0]
    assert row["result"] == "error"
    assert "ignored" in row["failure_reason"]
    assert "limit" in row["failure_reason"]
    assert row["job_id"] is not None


def test_run_with_disjoint_variables_across_files_succeeds(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """``--var`` declared by one file but not another must not fail the sibling load."""
    _seed_jt(fake_aap)
    suite_a = _write(
        tmp_path / "a.yml",
        "---\nvariables:\n  env: { type: string }\n---\n"
        "kind: AwxTestSuite\nname: a\njobTemplate: Deploy app\n"
        "cases:\n  c:\n    launch:\n      limit: '{{ env }}'\n",
    )
    suite_b = _write(
        tmp_path / "b.yml",
        "---\nvariables:\n  region: { type: string }\n---\n"
        "kind: AwxTestSuite\nname: b\njobTemplate: Deploy app\n"
        "cases:\n  c:\n    launch:\n      limit: '{{ region }}'\n",
    )

    result = cli.invoke(
        app,
        [
            "test",
            "run",
            str(suite_a),
            str(suite_b),
            "--var",
            "env=prod",
            "--var",
            "region=us-east-1",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0, result.stderr or result.output
    payloads = [body for _, _, action, body in fake_aap.actions_called if action == "launch"]
    limits = sorted(p["limit"] for p in payloads)
    assert limits == ["prod", "us-east-1"]


def test_run_against_directory_picks_up_yaml_children(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """Passing a directory should expand to its ``*.yml`` children."""
    _seed_jt(fake_aap)
    test_dir = tmp_path / "suites"
    test_dir.mkdir()
    _write(
        test_dir / "first.yml",
        "kind: AwxTestSuite\nname: f\njobTemplate: Deploy app\ncases:\n  c:\n    launch: {}\n",
    )
    _write(
        test_dir / "second.yaml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\ncases:\n  c:\n    launch: {}\n",
    )
    # Non-YAML siblings must be ignored.
    (test_dir / "README.md").write_text("# notes\n")

    result = cli.invoke(app, ["test", "run", str(test_dir), "--non-interactive"])

    assert result.exit_code == 0, result.stderr or result.output
    launches = [a for _, _, a, _ in fake_aap.actions_called if a == "launch"]
    assert len(launches) == 2  # one per YAML file


def test_run_filters_to_one_case(cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path) -> None:
    _seed_jt(fake_aap)
    test_file = _write(
        tmp_path / "matrix.yml",
        "kind: AwxTestSuite\n"
        "name: matrix\n"
        "jobTemplate: Deploy app\n"
        "cases:\n"
        "  keep:\n    launch: {}\n"
        "  skip:\n    launch: {}\n",
    )

    result = cli.invoke(app, ["test", "run", str(test_file), "--case", "keep", "--non-interactive"])

    assert result.exit_code == 0, result.stderr or result.output
    launch_count = sum(1 for _, _, action, _ in fake_aap.actions_called if action == "launch")
    assert launch_count == 1


def test_run_fails_when_required_var_missing(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    test_file = _write(
        tmp_path / "needs_var.yml",
        "---\n"
        "variables:\n"
        "  env: { type: string }\n"
        "---\n"
        "kind: AwxTestSuite\n"
        "name: needs_var\n"
        "jobTemplate: Deploy app\n"
        "cases:\n"
        "  c:\n    launch:\n      limit: '{{ env }}'\n",
    )

    result = cli.invoke(app, ["test", "run", str(test_file), "--non-interactive"])

    assert result.exit_code != 0
    assert "env" in (result.stderr or result.output)


def test_run_uses_var_flag(cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path) -> None:
    _seed_jt(fake_aap)
    test_file = _write(
        tmp_path / "with_var.yml",
        "---\n"
        "variables:\n"
        "  env: { type: string }\n"
        "---\n"
        "kind: AwxTestSuite\n"
        "name: with_var\n"
        "jobTemplate: Deploy app\n"
        "cases:\n"
        "  c:\n    launch:\n      limit: '{{ env }}'\n",
    )

    result = cli.invoke(
        app,
        ["test", "run", str(test_file), "--var", "env=prod", "--non-interactive"],
    )

    assert result.exit_code == 0, result.stderr or result.output
    payloads = [body for _, _, action, body in fake_aap.actions_called if action == "launch"]
    assert payloads and payloads[0]["limit"] == "prod"


def test_validate_renders_without_launching(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    test_file = _write(
        tmp_path / "v.yml",
        "kind: AwxTestSuite\n"
        "name: v\n"
        "jobTemplate: Deploy app\n"
        "cases:\n  c:\n    launch:\n      limit: x\n",
    )

    result = cli.invoke(app, ["test", "validate", str(test_file), "--non-interactive"])

    assert result.exit_code == 0, result.stderr or result.output
    # No launches issued
    assert all(action != "launch" for _, _, action, _ in fake_aap.actions_called)


def test_show_logs_prints_stdout_tail_for_failed_case(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """``--show-logs`` dumps the AWX stdout tail to stderr on failure."""
    _seed_jt(fake_aap)
    fake_aap.next_action_status = "failed"
    fake_aap.next_action_stdout = "line-1\nline-2\nERROR: boom\n"

    test_file = _write(
        tmp_path / "fail.yml",
        "kind: AwxTestSuite\nname: f\njobTemplate: Deploy app\ncases:\n  c:\n    launch: {}\n",
    )

    result = cli.invoke(
        app,
        ["test", "run", str(test_file), "--non-interactive", "--show-logs"],
    )

    assert result.exit_code == 1
    assert "ERROR: boom" in (result.stderr or result.output)


def test_list_json_includes_variable_metadata(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """``list --format json`` must surface declared frontmatter variables."""
    _seed_jt(fake_aap)
    test_file = _write(
        tmp_path / "with_vars.yml",
        "---\n"
        "variables:\n"
        "  env:\n"
        "    description: Target environment\n"
        "    type: choice\n"
        "    choices: [dev, prod]\n"
        "    default: dev\n"
        "---\n"
        "kind: AwxTestSuite\n"
        "name: deploy\n"
        "jobTemplate: Deploy app\n"
        "cases:\n  c:\n    launch: {}\n",
    )

    result = cli.invoke(
        app,
        ["test", "list", str(test_file), "--format", "json", "--non-interactive"],
    )

    assert result.exit_code == 0, result.stderr or result.output
    import json

    parsed = json.loads(result.stdout)
    assert parsed[0]["variables"]["env"]["description"] == "Target environment"
    assert parsed[0]["variables"]["env"]["choices"] == ["dev", "prod"]
    assert parsed[0]["cases"] == ["c"]


def test_list_dumps_cases_in_json(cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path) -> None:
    _seed_jt(fake_aap)
    test_file = _write(
        tmp_path / "list.yml",
        "kind: AwxTestSuite\n"
        "name: list-suite\n"
        "jobTemplate: Deploy app\n"
        "cases:\n  a:\n    launch: {}\n  b:\n    launch: {}\n",
    )

    result = cli.invoke(
        app,
        ["test", "list", str(test_file), "--format", "json", "--non-interactive"],
    )

    assert result.exit_code == 0, result.stderr or result.output
    out = result.stdout
    assert "a" in out and "b" in out


def _smoke(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "smoke.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\ncases:\n  c:\n    launch: {}\n",
    )


def _cancelled_ids(fake: FakeAap) -> list[int]:
    return [id_ for _, id_, action, _ in fake.actions_called if action == "cancel"]


@pytest.fixture
def running_job(fake_aap: FakeAap, monkeypatch: pytest.MonkeyPatch) -> FakeAap:
    """The next launch stays running; polling does not sleep."""
    monkeypatch.setattr(AwxContext, "pause", lambda self, seconds: None)
    _seed_jt(fake_aap)
    fake_aap.next_action_status = "running"
    return fake_aap


@pytest.mark.parametrize(
    ("flags", "reason", "cancels"),
    [([], "cancel requested", True), (["--no-cancel"], "it keeps running", False)],
)
def test_run_timeout_cancels_the_job_unless_no_cancel(
    cli: CliInvoker,
    running_job: FakeAap,
    tmp_path: Path,
    flags: list[str],
    reason: str,
    cancels: bool,
) -> None:
    result = cli.invoke(
        app, ["test", "run", str(_smoke(tmp_path)), "--timeout", "0.01", *flags, "-f", "json"]
    )

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    assert row["result"] == "timeout"
    assert row["failure_reason"] == f"still running after --timeout 0.01s; {reason}"
    assert _cancelled_ids(running_job) == ([row["job_id"]] if cancels else [])


@pytest.mark.parametrize("value", ["0", "-1"])
def test_run_rejects_a_non_positive_timeout(cli: CliInvoker, tmp_path: Path, value: str) -> None:
    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "--timeout", value])
    assert result.exit_code == 2, result.output


def test_run_timeout_and_parallel_default_to_settings(
    cli: CliInvoker, running_job: FakeAap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}
    real_call = RunTestSuite.__call__

    def spy(self: RunTestSuite, suites: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return real_call(self, suites, **kwargs)

    monkeypatch.setattr(RunTestSuite, "__call__", spy)
    monkeypatch.setenv("UNTAPED_AWX__TEST_TIMEOUT", "0.01")
    monkeypatch.setenv("UNTAPED_AWX__TEST_PARALLEL", "3")

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-f", "json"])

    assert result.exit_code == 1, result.output
    assert (seen["timeout"], seen["default_timeout"], seen["parallel"]) == (None, 0.01, 3)
    assert json.loads(result.stdout)[0]["result"] == "timeout"


def test_run_accepts_short_parallel_flag(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-j", "2"])
    assert result.exit_code == 0, result.output
    assert cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-j", "0"]).exit_code == 2


def test_run_interrupt_cancels_running_jobs(
    cli: CliInvoker, running_job: FakeAap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(self: WatchJob, job: Any, *, timeout: float | None = None) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(WatchJob, "__call__", interrupt)

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path))])

    assert result.exit_code == 130, result.output
    [job_id] = _cancelled_ids(running_job)
    assert f"interrupted: job {job_id} cancel requested" in result.stderr
    assert "jobs wait" not in result.stderr


def test_run_checks_expectations_and_reports_them(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    fake_aap.next_action_status = "failed"
    fake_aap.next_action_stdout = "TASK [check]\nfatal: [web1]: FAILED! => msg: disk full\n"
    test_file = _write(
        tmp_path / "neg.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\ncases:\n"
        "  c:\n    expect:\n      status: failed\n      log: {contains: [disk full]}\n",
    )

    result = cli.invoke(app, ["test", "run", str(test_file), "-f", "json"])

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert row["result"] == "pass"
    assert [check["check"] for check in row["expectations"]] == ["status", "log.contains"]
    assert row["job_url"] == f"https://aap.example.com/#/jobs/playbook/{row['job_id']}/output"


def test_run_table_hides_evidence_columns(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    fake_aap.next_action_status = "failed"
    fake_aap.next_action_stdout = "boom\n"

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path))])

    assert result.exit_code == 1, result.output
    assert "expected status successful, got failed" in result.stdout
    assert "log_tail" not in result.stdout
    assert "expectations" not in result.stdout
