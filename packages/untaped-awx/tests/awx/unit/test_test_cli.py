"""End-to-end CLI tests for ``awx test`` (run, list, validate)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from untaped.testing import CliInvoker
from untaped_awx.application import WatchJob
from untaped_awx.application.suites.runner import RunTestSuite
from untaped_awx.cli import app
from untaped_awx.cli.context import AwxContext

if TYPE_CHECKING:  # pragma: no cover — pytest --import-mode=importlib hides 'tests'
    from awx.conftest import FakeAap
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
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    # Not in the file: a plaintext token there warns on every run.
    monkeypatch.setenv("UNTAPED_AWX__TOKEN", "secret")
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


@pytest.mark.parametrize("path", [[], ["run"], ["list"], ["validate"], ["init"], ["prune"]])
def test_experimental_commands_say_so_in_help(cli: CliInvoker, path: list[str]) -> None:
    # the README's Versioning section promises every experimental command says so in --help.
    result = cli.invoke(app, ["test", *path, "--help"])
    assert result.exit_code == 0, result.output
    assert "Experimental: may change in a minor release." in result.stdout


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
    assert result.exit_code == 1
    combined = (result.stderr or "") + (result.output or "")
    assert "Traceback" not in combined
    assert f"--vars-file file {bad_vars} is invalid YAML" in result.stderr


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


@pytest.mark.parametrize(("status", "exit_code"), [(401, 4), (403, 4), (503, 5), (400, 1)])
def test_run_exits_with_the_category_of_a_launch_failure(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, status: int, exit_code: int
) -> None:
    """A rejected token (4) or an unavailable AWX (5) is not a failed test (1)."""
    _seed_jt(fake_aap)
    test_file = _write(
        tmp_path / "smoke.yml",
        "kind: AwxTestSuite\nname: smoke\njobTemplate: Deploy app\ncases:\n  one: {}\n",
    )
    fake_aap.action_error = status

    result = cli.invoke(app, ["test", "run", str(test_file), "--non-interactive", "-f", "json"])

    assert result.exit_code == exit_code, result.stderr
    [row] = json.loads(result.stdout)
    assert row["result"] == "error"
    failure = row["failure"]
    assert failure["system"] == {401: "awx.credentials", 403: "awx.credentials"}.get(
        status, "awx.suite" if status == 400 else "awx.controller"
    )
    assert failure["retryable"] is (status == 503)
    if status == 401:
        assert "untaped auth set awx" in failure["hint"]


def test_run_preflights_every_case_before_launching(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """A limit AWX would ignore runs against the whole inventory: nothing launches."""
    fake_aap.seed("job_templates", name="Deploy app")
    test_file = _write(
        tmp_path / "smoke.yml",
        "kind: AwxTestSuite\nname: smoke\njobTemplate: Deploy app\n"
        "cases:\n  one:\n    launch:\n      limit: web-*\n  two: {}\n",
    )

    result = cli.invoke(app, ["test", "run", str(test_file), "--non-interactive"])

    assert result.exit_code == 1
    assert "preflight failed, nothing launched:" in result.stderr
    assert "smoke/one: " in result.stderr
    assert "ask_limit_on_launch is false" in result.stderr
    assert "smoke/two" not in result.stderr
    assert fake_aap.actions_called == []


def test_run_errors_when_awx_ignores_launch_fields(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """AWX can still ignore a field the preflight let through: not a pass."""
    _seed_jt(fake_aap)
    fake_aap.next_action_ignored_fields = {"limit": "web-*"}
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
    assert (row["failure"]["system"], row["failure"]["category"]) == ("awx.suite", "invalid")
    assert "ignored" in row["failure"]["message"]
    assert "limit" in row["failure"]["message"]
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
    """A directory expands to every ``*.yml``/``*.yaml`` file under it."""
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
    (test_dir / "nested").mkdir()
    _write(
        test_dir / "nested" / "third.yml",
        "kind: AwxTestSuite\nname: t\njobTemplate: Deploy app\ncases:\n  c:\n    launch: {}\n",
    )
    # Non-YAML siblings must be ignored.
    (test_dir / "README.md").write_text("# notes\n")

    result = cli.invoke(app, ["test", "run", str(test_dir), "--non-interactive"])

    assert result.exit_code == 0, result.stderr or result.output
    launches = [a for _, _, a, _ in fake_aap.actions_called if a == "launch"]
    assert len(launches) == 3  # one per YAML file, nested ones included


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

    # A missing --var is a usage error (2), not a broken environment (4).
    assert result.exit_code == 2
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


def test_validate_reports_launches_awx_would_reject(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    fake_aap.seed("job_templates", name="Deploy app")
    test_file = _write(
        tmp_path / "v.yml",
        "kind: AwxTestSuite\nname: v\njobTemplate: Deploy app\n"
        "cases:\n  c:\n    launch:\n      limit: x\n  ok: {}\n",
    )

    result = cli.invoke(app, ["test", "validate", str(test_file), "--non-interactive"])

    assert result.exit_code == 1
    assert "error: v/c: " in result.stderr
    assert "ask_limit_on_launch is false" in result.stderr
    assert "v/ok" not in result.stderr


def test_validate_reports_each_case_as_an_attributed_error(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_aap.seed("job_templates", name="Deploy app")
    test_file = _write(
        tmp_path / "v.yml",
        "kind: AwxTestSuite\nname: v\njobTemplate: Deploy app\n"
        "cases:\n  c:\n    launch:\n      limit: x\n",
    )
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")

    result = cli.invoke(app, ["test", "validate", str(test_file), "--non-interactive"])

    [line] = [json.loads(line) for line in result.stderr.splitlines()]
    assert (line["item"], line["category"], line["system"]) == ("v/c", "invalid", "awx")


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
    parsed = json.loads(result.stdout)
    assert parsed[0]["variables"]["env"]["description"] == "Target environment"
    assert parsed[0]["variables"]["env"]["choices"] == ["dev", "prod"]


def test_list_emits_one_row_per_case_in_every_format(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    test_file = _write(
        tmp_path / "list.yml",
        "kind: AwxTestSuite\nname: list-suite\njobTemplate: Deploy app\norganization: Ops\n"
        "cases:\n  a:\n    launch: {}\n  b:\n    launch: {}\n",
    )

    result = cli.invoke(app, ["test", "list", str(test_file), "-f", "json"])

    assert result.exit_code == 0, result.stderr or result.output
    assert json.loads(result.stdout) == [
        {
            "suite": "list-suite",
            "case": case,
            "job_template": "Deploy app",
            "workflow_template": None,
            "organization": "Ops",
            "path": str(test_file),
            "variables": {},
        }
        for case in ("a", "b")
    ]
    table = cli.invoke(app, ["test", "list", str(test_file)])
    assert table.exit_code == 0, table.output
    assert "list-suite" in table.stdout and "variables" not in table.stdout


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
    assert row["failure"]["system"] == "awx.playbook"
    assert row["failure"]["message"] == f"still running after 0.01s; {reason}"
    assert _cancelled_ids(running_job) == ([row["job_id"]] if cancels else [])


@pytest.mark.parametrize(
    ("flags", "reason", "cancels"),
    [([], "cancel requested", True), (["--no-cancel"], "it keeps running", False)],
)
def test_run_cancels_a_job_launched_with_ignored_fields_unless_no_cancel(
    cli: CliInvoker,
    running_job: FakeAap,
    tmp_path: Path,
    flags: list[str],
    reason: str,
    cancels: bool,
) -> None:
    """The case errors on what AWX ignored; the job it launched anyway is abandoned."""
    running_job.next_action_ignored_fields = {"limit": "web-*"}

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), *flags, "-f", "json"])

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    assert row["result"] == "error"
    assert (row["failure"]["system"], row["failure"]["category"]) == ("awx.suite", "invalid")
    assert row["failure"]["message"].endswith(f"AWX ignored launch fields: limit; {reason}")
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
    assert row["job_url"].endswith(f"/{row['job_id']}/output")


def test_run_reports_the_job_start_and_finish_as_utc_timestamps(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    fake_aap.next_action_job_fields = {
        "started": "2026-01-02T03:04:05.123456Z",
        "finished": "2026-01-02T03:05:06.654321Z",
    }
    test_file = _write(
        tmp_path / "t.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\ncases:\n  c: {}\n",
    )

    result = cli.invoke(app, ["test", "run", str(test_file), "-f", "json"])

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert (row["started_at"], row["finished_at"]) == (
        "2026-01-02T03:04:05Z",
        "2026-01-02T03:05:06Z",
    )


def test_run_table_hides_evidence_columns(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    fake_aap.next_action_status = "failed"
    fake_aap.next_action_stdout = "boom\n"

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path))])

    assert result.exit_code == 1, result.output
    assert "awx.playbook" in result.stdout
    assert "failed without a failed task" in result.stdout
    assert "evidence" not in result.stdout
    assert "expectations" not in result.stdout
    # attribution needs the failed events and host summaries; the table never reads a log
    paths = [call.request.url.path for call in fake_aap.router.calls]
    assert not any(path.endswith("/stdout/") for path in paths)
    assert not any(
        call.request.url.params.get("order_by") == "-counter" for call in fake_aap.router.calls
    )
    assert sum(path.endswith("/job_host_summaries/") for path in paths) == 1


def test_run_reports_results_despite_an_unknown_column(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """Jobs already ran: a typo'd ``--columns`` must not swallow their results."""
    _seed_jt(fake_aap)
    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-c", "resutl,result"])
    assert result.exit_code == 0, result.output
    assert "pass" in result.stdout


# ---- failed tasks, --scm-branch ------------------------------------------


def _failing_job(fake: FakeAap, event: dict[str, Any]) -> None:
    _seed_jt(fake)
    fake.next_action_status = "failed"
    fake.next_action_events = [
        {"event": "runner_on_ok", "failed": False, "host_name": "web1", "task": "Setup"},
        {"failed": True, "host_name": "web1", "task": "Migrate", "stdout": "boom", **event},
    ]


def test_run_reports_failed_tasks_from_job_events(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _failing_job(fake_aap, {"event": "runner_on_failed", "event_data": {"res": {"msg": "no"}}})

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-f", "json"])

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    failure = row["failure"]
    assert (failure["system"], failure["message"]) == (
        "awx.playbook",
        "task 'Migrate' failed on web1: no",
    )
    assert [task["msg"] for task in failure["evidence"]["failed_tasks"]] == ["no"]
    assert failure["evidence"]["log_tail"] == ["boom"]


def test_show_logs_prints_failed_tasks_under_their_case(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _failing_job(
        fake_aap,
        {"event": "runner_on_unreachable", "event_data": {"res": {"stderr": "ssh timeout"}}},
    )

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "--show-logs"])

    assert result.exit_code == 5, result.output  # unreachable hosts: retry later
    lines = result.stderr.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("--- s/c job "))
    assert lines[start].endswith(": awx.hosts: unreachable: web1: ssh timeout")
    assert lines[start + 1 : start + 4] == [
        "--- last 1 log line",
        "unreachable: [web1] Migrate: ssh timeout",
        "boom",
    ]


def _dependency_failed(fake: FakeAap, kind: str, update_id: int) -> None:
    """The next job ends in error because the named update failed first."""
    _seed_jt(fake)
    fake.next_action_status = "error"
    fake.next_action_job_fields = {
        "job_explanation": "Previous Task Failed: "
        + json.dumps({"job_type": kind, "job_name": "acme", "job_id": str(update_id)}),
    }
    fake.seed(f"{kind}s", id=update_id, name="acme", status="failed")
    for counter, event in enumerate(
        [
            {"event": "runner_on_ok", "stdout": "ok: [localhost]"},
            {
                "event": "runner_on_failed",
                "failed": True,
                "host_name": "localhost",
                "task": "Update git repository",
                "stdout": "\x1b[0;31mfatal: [localhost]: FAILED!\x1b[0m",
                "event_data": {"res": {"msg": "couldn't find remote ref feature/x"}},
            },
        ],
        start=1,
    ):
        fake.seed(f"{kind}_events", **{kind: update_id}, counter=counter, **event)


@pytest.mark.parametrize(
    ("kind", "system", "exit_code"),
    [("project_update", "awx.scm", 1), ("inventory_update", "awx.inventory", 4)],
)
def test_run_blames_the_update_that_failed_before_the_job(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, kind: str, system: str, exit_code: int
) -> None:
    _dependency_failed(fake_aap, kind, 812)

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-f", "json"])

    assert result.exit_code == exit_code, result.output
    [row] = json.loads(result.stdout)
    failure = row["failure"]
    assert failure["system"] == system
    assert failure["message"] == (
        f"{kind.replace('_', ' ')} 812 for 'acme' failed: couldn't find remote ref feature/x"
    )
    evidence = failure["evidence"]
    assert evidence["related"]["kind"] == kind
    assert evidence["related"]["id"] == 812
    assert evidence["log_tail"] == ["ok: [localhost]", "fatal: [localhost]: FAILED!"]
    assert [task["task"] for task in evidence["failed_tasks"]] == ["Update git repository"]
    assert evidence["job_explanation"].startswith("Previous Task Failed: ")


def test_show_logs_prints_the_failed_updates_evidence(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _dependency_failed(fake_aap, "project_update", 812)

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "--show-logs"])

    assert result.exit_code == 1, result.output
    lines = result.stderr.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("--- s/c job "))
    assert lines[start].endswith(": awx.scm: project update 812 for 'acme' failed: "
                                 "couldn't find remote ref feature/x")  # fmt: skip
    assert lines[start + 1] == "--- last 2 log lines of project_update 812"


def test_show_logs_names_a_launch_failure_without_a_job(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    fake_aap.action_error = 503

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "--show-logs"])

    assert result.exit_code == 5, result.output
    [line] = [line for line in result.stderr.splitlines() if line.startswith("--- ")]
    assert line.startswith("--- s/c: awx.controller: ")


def test_run_reports_every_hosts_summary_in_structured_output(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    fake_aap.next_action_host_summaries = [
        {"host_name": "web2", "ok": 4, "changed": 0, "failures": 0, "dark": 1},
        {"host_name": "web1", "ok": 5, "changed": 2, "failures": 0, "dark": 0, "skipped": 1},
    ]

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-f", "json"])

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert list(row["hosts"]) == ["web1", "web2"]
    assert row["hosts"]["web1"] == {
        "ok": 5,
        "changed": 2,
        "failed": 0,
        "unreachable": 0,
        "skipped": 1,
        "rescued": 0,
        "ignored": 0,
    }
    assert row["hosts"]["web2"]["unreachable"] == 1
    assert (row["hosts_truncated"], row["failure"]) == (False, None)


@pytest.fixture
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AwxContext, "pause", lambda self, seconds: None)


def _failed_task_job(fake: FakeAap, *, unsaved_reads: int) -> None:
    _seed_jt(fake)
    fake.next_action_status = "failed"
    fake.next_action_unsaved_reads = unsaved_reads
    fake.next_action_events = [
        {"event": "runner_on_failed", "failed": True, "host_name": "web1", "task": "Migrate",
         "event_data": {"res": {"msg": "no"}}},
    ]  # fmt: skip
    fake.next_action_host_summaries = [{"host_name": "web1", "failures": 1, "failed": True}]


def test_run_waits_for_awx_to_save_the_events_before_attributing(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, no_pause: None
) -> None:
    _failed_task_job(fake_aap, unsaved_reads=2)

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-f", "json"])

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    assert (row["failure"]["system"], row["failure"]["message"]) == (
        "awx.playbook",
        "task 'Migrate' failed on web1: no",
    )
    assert row["hosts"]["web1"]["failed"] == 1


def test_run_never_blames_the_playbook_for_events_awx_has_not_saved(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, no_pause: None
) -> None:
    _failed_task_job(fake_aap, unsaved_reads=50)

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-f", "json"])

    assert result.exit_code == 5, result.output
    [row] = json.loads(result.stdout)
    assert row["failure"]["system"] == "awx.controller"
    assert "AWX has not processed its events yet" in row["failure"]["message"]


def _case(name: str, body: str = "{}") -> str:
    return f"  {name}: {body}\n"


@pytest.mark.parametrize(
    ("job", "expect", "system", "exit_code"),
    [
        ({"job_explanation": "Failed to pull image quay.io/ee"}, "", "awx.controller", 5),
        (
            {"result_traceback": "Traceback\nCredentialLookupError: vault: permission denied"},
            "",
            "awx.credentials",
            4,
        ),
        ({}, "{expect: {status: error, log: {contains: [nope]}}}", "awx.controller", 5),
    ],
)
def test_run_attributes_a_job_that_ended_in_error(
    cli: CliInvoker,
    fake_aap: FakeAap,
    tmp_path: Path,
    job: dict[str, Any],
    expect: str,
    system: str,
    exit_code: int,
) -> None:
    _seed_jt(fake_aap)
    fake_aap.next_action_status = "error"
    fake_aap.next_action_job_fields = job
    suite = _write(
        tmp_path / "e.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\ncases:\n"
        + _case("c", expect or "{}"),
    )

    result = cli.invoke(app, ["test", "run", str(suite), "-f", "json"])

    assert result.exit_code == exit_code, result.output
    [row] = json.loads(result.stdout)
    assert row["failure"]["system"] == system


def test_run_blames_the_controller_for_a_job_stuck_pending(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, no_pause: None
) -> None:
    _seed_jt(fake_aap)
    fake_aap.next_action_status = "pending"

    result = cli.invoke(
        app, ["test", "run", str(_smoke(tmp_path)), "--timeout", "0.01", "-f", "json"]
    )

    assert result.exit_code == 5, result.output
    [row] = json.loads(result.stdout)
    assert (row["result"], row["failure"]["system"]) == ("timeout", "awx.controller")


def test_run_blames_the_expectation_when_the_job_ran_as_asked(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    suite = _write(
        tmp_path / "neg.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\ncases:\n"
        + _case("c", "{expect: {status: failed}}"),
    )

    result = cli.invoke(app, ["test", "run", str(suite), "-f", "json"])

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    assert (row["failure"]["system"], row["failure"]["message"]) == (
        "awx.expectation",
        "expected status failed, got successful",
    )


def test_run_exits_with_the_most_severe_case(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, no_pause: None
) -> None:
    """A playbook failure (1), unreachable hosts (5) and a failed inventory sync (4): exit 4."""
    _seed_jt(fake_aap)
    fake_aap.seed("inventory_updates", id=900, name="Cloud", status="failed")
    outcomes: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = [
        ("failed", {}, [{"event": "runner_on_failed", "failed": True, "host_name": "a"}]),
        ("failed", {}, [{"event": "runner_on_unreachable", "failed": True, "host_name": "b"}]),
        (
            "failed",
            {"job_explanation": 'Previous Task Failed: {"job_type": "inventory_update", '
                                '"job_name": "Cloud", "job_id": "900"}'},
            [],
        ),
    ]  # fmt: skip
    real_action = fake_aap._action

    def action(*args: Any) -> Any:
        status, fields, events = outcomes.pop(0)
        fake_aap.next_action_status = status
        fake_aap.next_action_job_fields = fields
        fake_aap.next_action_events = events
        return real_action(*args)

    fake_aap._action = action  # type: ignore[method-assign]
    suite = _write(
        tmp_path / "m.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\ncases:\n"
        + _case("playbook")
        + _case("hosts")
        + _case("inventory"),
    )

    result = cli.invoke(app, ["test", "run", str(suite), "-f", "json"])

    assert result.exit_code == 4, result.output
    # Cases may launch in parallel, so which case got which job varies.
    systems = sorted(row["failure"]["system"] for row in json.loads(result.stdout))
    assert systems == ["awx.hosts", "awx.inventory", "awx.playbook"]


def test_run_cuts_host_summaries_above_500_hosts(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    fake_aap.next_action_host_summaries = [
        {"host_name": f"h{index:03}", "ok": 1} for index in range(501)
    ]

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "-f", "json"])

    [row] = json.loads(result.stdout)
    assert (len(row["hosts"]), row["hosts_truncated"]) == (500, True)


def _branchable(fake: FakeAap, *, ask_scm_branch: bool = True) -> None:
    project = fake.seed("projects", name="app", allow_override=True, scm_revision="c0ffee")
    fake.seed(
        "job_templates",
        name="Deploy app",
        project=project["id"],
        scm_branch="",
        ask_scm_branch_on_launch=ask_scm_branch,
    )


def test_run_scm_branch_runs_every_case_on_that_ref(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _branchable(fake_aap)

    result = cli.invoke(
        app, ["test", "run", str(_smoke(tmp_path)), "--scm-branch", "fix", "-f", "json"]
    )

    assert result.exit_code == 0, result.output
    [(_, _, _, body)] = fake_aap.actions_called
    assert body["scm_branch"] == "fix"
    [row] = json.loads(result.stdout)
    assert (row["scm_branch"], row["scm_revision"]) == ("fix", "c0ffee")


def test_run_scm_branch_needs_templates_that_prompt_for_it(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _branchable(fake_aap, ask_scm_branch=False)

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "--scm-branch", "fix"])

    assert result.exit_code == 1
    assert "s/c: " in result.stderr
    assert "ask_scm_branch_on_launch is false" in result.stderr
    assert "drop scm_branch" in result.stderr
    assert fake_aap.actions_called == []


def _saved_defaults(fake: FakeAap) -> None:
    """A template that prompts for nothing: it saves ``env: prod`` and inherits ``main``."""
    project = fake.seed(
        "projects", name="app", scm_branch="main", allow_override=False, scm_revision="c0ffee"
    )
    fake.seed(
        "job_templates",
        name="Deploy app",
        project=project["id"],
        scm_branch="",
        extra_vars="env: prod\nregion: eu\n",
    )


@pytest.mark.parametrize(
    ("launch", "flags"),
    [
        ("{extra_vars: {env: prod}}", []),
        ("{scm_branch: main}", []),
        ("{}", ["--scm-branch", "main"]),
    ],
)
def test_run_accepts_launch_values_the_template_already_has(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, launch: str, flags: list[str]
) -> None:
    """AWX drops them from the launch; so does the run, and its rerun pins no commit."""
    _saved_defaults(fake_aap)
    suite = _write(
        tmp_path / "s.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\n"
        f"cases:\n  c: {{launch: {launch}, expect: {{idempotent: true}}}}\n",
    )

    result = cli.invoke(app, ["test", "run", str(suite), *flags, "-f", "json"])

    assert result.exit_code == 0, result.stderr
    [row] = json.loads(result.stdout)
    assert row["result"] == "pass"
    assert [body for *_, body in fake_aap.actions_called] == [{}, {}]


@pytest.mark.parametrize(
    ("launch", "field"),
    [
        ("{extra_vars: {env: prod, region: us}}", "extra_vars"),
        ("{extra_vars: {env: prod, tier: web}}", "extra_vars"),
        ("{scm_branch: fix}", "scm_branch"),
    ],
)
def test_run_still_refuses_a_value_the_template_does_not_have(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, launch: str, field: str
) -> None:
    _saved_defaults(fake_aap)
    suite = _write(
        tmp_path / "s.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\n"
        f"cases:\n  c: {{launch: {launch}}}\n",
    )

    result = cli.invoke(app, ["test", "run", str(suite)])

    assert result.exit_code == 1
    assert f"does not prompt for {field} on launch" in result.stderr
    assert fake_aap.actions_called == []


def test_run_scm_branch_head_runs_the_pushed_branch(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped_awx.infrastructure import git_head

    _branchable(fake_aap)
    monkeypatch.setattr(git_head, "pushed_branch", lambda cwd=None: "feature/x")

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path)), "--scm-branch", "HEAD"])

    assert result.exit_code == 0, result.output
    [(_, _, _, body)] = fake_aap.actions_called
    assert body["scm_branch"] == "feature/x"


# ---- discovery, selection, summary ---------------------------------------


def _suite_text(name: str, *, organization: str | None = None) -> str:
    org = f"organization: {organization}\n" if organization else ""
    return (
        f"kind: AwxTestSuite\nname: {name}\njobTemplate: Deploy app\n{org}"
        "cases:\n  smoke: {}\n  full: {}\n"
    )


def test_run_without_paths_runs_every_suite_under_the_repository(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    _seed_jt(fake_aap)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tests_dir = tmp_path / ".untaped" / "awx" / "tests"
    (tests_dir / "web").mkdir(parents=True)
    _write(tests_dir / "a.yml", _suite_text("a"))
    _write(tests_dir / "web" / "b.yaml", _suite_text("b"))
    (tmp_path / "src").mkdir()
    monkeypatch.chdir(tmp_path / "src")

    result = cli.invoke(app, ["test", "run", "--case", "b/smoke", "-f", "json"])

    assert result.exit_code == 0, result.output
    assert [(row["suite"], row["case"]) for row in json.loads(result.stdout)] == [("b", "smoke")]


def test_no_paths_and_no_tests_directory_is_a_usage_error(
    cli: CliInvoker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = cli.invoke(app, ["test", "list"])
    assert result.exit_code == 2
    assert f"no test paths given and no {tmp_path / '.untaped/awx/tests'}" in result.stderr


def test_no_paths_without_git_names_the_failure_instead_of_guessing(
    cli: CliInvoker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda _: None)

    result = cli.invoke(app, ["test", "list"])

    # A missing git binary is a local setup problem: exit 4.
    assert result.exit_code == 4
    assert "`git` not found on PATH; pass test paths explicitly" in result.stderr
    assert "Traceback" not in result.output


def test_overlapping_paths_read_each_file_once(cli: CliInvoker, tmp_path: Path) -> None:
    suite = _write(tmp_path / "one.yml", _suite_text("one"))

    result = cli.invoke(app, ["test", "list", str(tmp_path), str(suite), "-f", "json"])

    assert result.exit_code == 0, result.output
    assert [row["case"] for row in json.loads(result.stdout)] == ["smoke", "full"]


def test_suite_names_must_be_unique(cli: CliInvoker, tmp_path: Path) -> None:
    first = _write(tmp_path / "one.yml", _suite_text("dup"))
    second = _write(tmp_path / "two.yml", _suite_text("dup"))

    result = cli.invoke(app, ["test", "list", str(first), str(second)])

    assert result.exit_code == 1
    assert f"suite 'dup' is defined in both {first} and {second}" in result.stderr


def test_a_suite_organization_picks_its_template(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    for org_id, org in ((1, "Default"), (2, "Ops")):
        fake_aap.seed("organizations", id=org_id, name=org)
        fake_aap.seed("job_templates", name="Deploy app", organization=org_id)
    ops_template = fake_aap.store["job_templates"][max(fake_aap.store["job_templates"])]
    test_file = _write(tmp_path / "ops.yml", _suite_text("ops", organization="Ops"))

    result = cli.invoke(app, ["test", "run", str(test_file), "--case", "smoke"])

    assert result.exit_code == 0, result.output
    assert {id_ for _, id_, _, _ in fake_aap.actions_called} == {ops_template["id"]}


def test_a_suite_organization_scopes_its_launch_names(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """``inventory: Web`` exists in both organizations; the suite's one is used."""
    inventories = {}
    for org_id, org in ((1, "Default"), (2, "Ops")):
        fake_aap.seed("organizations", id=org_id, name=org)
        fake_aap.seed(
            "job_templates", name="Deploy app", organization=org_id, ask_inventory_on_launch=True
        )
        inventories[org] = fake_aap.seed("inventories", name="Web", organization=org_id)["id"]
    test_file = _write(
        tmp_path / "ops.yml",
        "kind: AwxTestSuite\nname: ops\njobTemplate: Deploy app\norganization: Ops\n"
        "cases:\n  web:\n    launch:\n      inventory: Web\n",
    )

    validated = cli.invoke(app, ["test", "validate", str(test_file)])
    result = cli.invoke(app, ["test", "run", str(test_file)])

    assert validated.exit_code == 0, validated.output
    assert result.exit_code == 0, result.output
    [(_, _, _, body)] = fake_aap.actions_called
    assert body["inventory"] == inventories["Ops"]


def test_run_summarizes_results_on_stderr(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    fake_aap.next_action_status = "failed"
    test_file = _write(tmp_path / "s.yml", _suite_text("s"))

    result = cli.invoke(app, ["test", "run", str(test_file), "-f", "json"])

    assert result.exit_code == 1
    assert "2 cases: 1 pass, 1 fail" in result.stderr


# ---- regressions ---------------------------------------------------------


def _outcomes(fake: FakeAap, outcomes: list[dict[str, Any]]) -> None:
    """Each launch in turn gets the next outcome's ``next_action_*`` values."""
    real_action = fake._action

    def action(*args: Any) -> Any:
        if args[2] == "launch" and outcomes:
            for name, value in outcomes.pop(0).items():
                setattr(fake, f"next_action_{name}", value)
        return real_action(*args)

    fake._action = action  # type: ignore[method-assign]


def _run(cli: CliInvoker, *args: str) -> Any:
    return cli.invoke(app, ["test", "run", *args, "--parallel", "1"])


def _baseline(cli: CliInvoker, fake: FakeAap, suite: Path, out: Path, *statuses: str) -> Path:
    """Run ``suite`` once, its cases' jobs ending in ``statuses``, and save the JSON output."""
    _outcomes(fake, [{"status": status} for status in statuses])
    saved = _run(cli, str(suite), "-f", "json")
    out.write_text(saved.stdout)
    return out


def test_compare_marks_each_change_and_fails_only_on_a_regression(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    suite = _write(tmp_path / "s.yml", _suite_text("s"))
    saved = _baseline(cli, fake_aap, suite, tmp_path / "base.json", "successful", "failed")

    _outcomes(fake_aap, [{"status": "failed"}, {"status": "failed"}])
    result = _run(cli, str(suite), "--compare", str(saved), "-f", "json")

    assert result.exit_code == 1, result.output
    smoke, full = json.loads(result.stdout)
    before = json.loads(saved.read_text())
    assert (smoke["change"], smoke["baseline"]) == (
        "regression",
        {
            "result": "pass",
            "job_id": before[0]["job_id"],
            "system": None,
            "category": None,
            "node": None,
        },
    )
    assert (full["change"], full["baseline"]["system"]) == ("still_failing", "awx.playbook")
    assert "2 cases: 2 fail" in result.stderr
    assert "compared with the baseline: 1 regression, 1 still_failing" in result.stderr

    # A failure the baseline already had does not fail the run.
    _outcomes(fake_aap, [{"status": "successful"}, {"status": "failed"}])
    result = _run(cli, str(suite), "--compare", str(saved), "-f", "json")

    assert result.exit_code == 0, result.output
    assert [row["change"] for row in json.loads(result.stdout)] == ["pass", "still_failing"]


def test_compare_reads_pipe_output_and_reports_removed_cases_in_the_table(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    suite = _write(tmp_path / "s.yml", _suite_text("s"))
    saved = _run(cli, str(suite), "-f", "pipe")
    (tmp_path / "base.ndjson").write_text(saved.stdout)
    _write(suite, "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\ncases:\n  smoke: {}\n")

    result = _run(cli, str(suite), "--compare", str(tmp_path / "base.ndjson"), "-f", "json")

    assert result.exit_code == 0, result.output
    smoke, full = json.loads(result.stdout)
    assert (smoke["case"], smoke["change"]) == ("smoke", "pass")
    assert {key: full[key] for key in ("case", "result", "job_id", "change")} == {
        "case": "full",
        "result": None,
        "job_id": None,
        "change": "removed",
    }
    assert "1 case: 1 pass" in result.stderr
    assert "compared with the baseline: 1 pass, 1 removed" in result.stderr

    table = _run(cli, str(suite), "--compare", str(tmp_path / "base.ndjson"))
    header = table.stdout.splitlines()[1]
    assert header.index("result") < header.index("change") < header.index("job_status")
    assert "removed" in table.stdout


def test_compare_still_exits_for_the_environment(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    suite = _smoke(tmp_path)
    saved = _baseline(cli, fake_aap, suite, tmp_path / "base.json", "failed")

    _outcomes(fake_aap, [{"status": "error", "job_fields": {"job_explanation": "pod lost"}}])
    result = _run(cli, str(suite), "--compare", str(saved), "-f", "json")

    assert result.exit_code == 5, result.output
    [row] = json.loads(result.stdout)
    assert (row["change"], row["failure"]["system"]) == ("regression", "awx.controller")


def test_compare_fails_the_run_for_a_failing_new_case(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    saved = _baseline(cli, fake_aap, _smoke(tmp_path), tmp_path / "base.json", "successful")
    suite = _write(tmp_path / "s.yml", _suite_text("s"))

    _outcomes(fake_aap, [{"status": "failed"}, {"status": "successful"}])
    result = _run(cli, str(suite), "--compare", str(saved), "-f", "json")

    assert result.exit_code == 1, result.output
    assert [(row["case"], row["change"]) for row in json.loads(result.stdout)] == [
        ("smoke", "new"),
        ("full", "new"),
        ("c", "removed"),
    ]


def test_validate_refuses_an_idempotent_negative_case(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    suite = _write(
        tmp_path / "i.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\n"
        "cases:\n  c: {expect: {status: failed, idempotent: true}}\n",
    )
    result = cli.invoke(app, ["test", "validate", str(suite)])
    assert result.exit_code == 1
    assert "case 'c': idempotent needs status successful" in result.stderr


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ('[{"id": 1, "name": "Deploy"}]', "row 1 is not an awx.test_result row: suite"),
        ("suite: s\n", "Expecting value"),
        (
            '{"untaped": "1", "kind": "awx.job", "record": {"id": 1}}\n',
            "line 1: record kind 'awx.job' is not accepted here; expected 'awx.test_result'",
        ),
        ('{"untaped": "1", "kind": "awx.test_result"\n', "line 1: invalid JSON"),
    ],
)
def test_compare_refuses_a_file_that_is_not_run_output(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, content: str, problem: str
) -> None:
    _seed_jt(fake_aap)
    saved = _write(tmp_path / "base.json", content)

    result = _run(cli, str(_smoke(tmp_path)), "--compare", str(saved))

    assert result.exit_code == 1, result.output
    assert f"--compare file {saved} is not the output of" in result.stderr
    assert problem in result.stderr
    assert fake_aap.actions_called == []


def test_compare_needs_an_existing_file_and_excludes_baseline(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    missing = tmp_path / "none.json"
    result = _run(cli, str(_smoke(tmp_path)), "--compare", str(missing))
    assert result.exit_code == 2
    assert f"path does not exist: {missing}" in result.stderr

    saved = _write(tmp_path / "base.json", "[]")
    result = _run(cli, str(_smoke(tmp_path)), "--compare", str(saved), "--baseline", "main")
    assert result.exit_code == 2
    assert "--compare and --baseline cannot be combined" in result.stderr


def test_baseline_runs_every_case_at_the_ref_first_then_compares(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _branchable(fake_aap)
    _outcomes(fake_aap, [{"status": "failed"}, {"status": "successful"}])

    result = _run(
        cli, str(_smoke(tmp_path)), "--baseline", "main", "--scm-branch", "fix", "-f", "json"
    )

    assert result.exit_code == 0, result.output
    assert [body.get("scm_branch") for _, _, _, body in fake_aap.actions_called] == [
        "main",
        "fix",
    ]
    [row] = json.loads(result.stdout)
    first_job = fake_aap.list_records("jobs")[0]["id"]
    assert (row["change"], row["baseline"]["job_id"], row["scm_branch"]) == (
        "fixed",
        first_job,
        "fix",
    )


def test_baseline_head_is_the_pushed_branch(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped_awx.infrastructure import git_head

    _branchable(fake_aap)
    monkeypatch.setattr(git_head, "pushed_branch", lambda cwd=None: "feature/x")
    _outcomes(fake_aap, [{"status": "successful"}, {"status": "failed"}])

    result = _run(cli, str(_smoke(tmp_path)), "--baseline", "HEAD")

    assert result.exit_code == 1, result.output
    assert [body.get("scm_branch") for _, _, _, body in fake_aap.actions_called] == [
        "feature/x",
        None,
    ]
    assert "regression" in result.stdout


def test_an_environment_failure_of_the_baseline_run_still_counts(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _branchable(fake_aap)
    _outcomes(fake_aap, [{"status": "error"}, {"status": "successful"}])

    result = _run(cli, str(_smoke(tmp_path)), "--baseline", "main", "-f", "json")

    assert result.exit_code == 5, result.output
    [row] = json.loads(result.stdout)
    assert (row["result"], row["change"], row["baseline"]["system"]) == (
        "pass",
        "fixed",
        "awx.controller",
    )


def test_an_idempotent_case_lists_what_its_rerun_changed(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    events = [
        {"event": "runner_on_ok", "changed": False, "host_name": "web1", "task": "Check"},
        {"event": "runner_on_ok", "changed": True, "host_name": "web1", "task": "Write config"},
    ]
    _outcomes(
        fake_aap,
        [
            {"host_summaries": [{"host_name": "web1", "changed": 1}]},
            {"host_summaries": [{"host_name": "web1", "changed": 1}], "events": events},
        ],
    )
    suite = _write(
        tmp_path / "i.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\n"
        "cases:\n  c: {expect: {idempotent: true}}\n",
    )

    result = _run(cli, str(suite), "-f", "json")

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    assert row["rerun_job_id"] not in (None, row["job_id"])
    assert row["failure"]["message"] == ("not idempotent: the rerun ended successful, 1 changed")
    assert row["failure"]["evidence"]["changed_tasks"] == [{"host": "web1", "task": "Write config"}]


def test_validate_warns_about_a_negative_case_without_failed_tasks(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)
    suite = _write(
        tmp_path / "neg.yml",
        "kind: AwxTestSuite\nname: s\njobTemplate: Deploy app\n"
        "defaults: {expect: {status: failed}}\n"
        "cases:\n"
        "  bare: {}\n"
        "  proven: {expect: {failed_tasks: [{msg: must be set}]}}\n"
        "  positive: {expect: {status: successful}}\n",
    )

    result = cli.invoke(app, ["test", "validate", str(suite)])

    assert result.exit_code == 0, result.output
    warnings = [line for line in result.stderr.splitlines() if line.startswith("warning:")]
    assert warnings == [
        "warning: s/bare: expects status failed without failed_tasks, so a failure for "
        "another reason passes it"
    ]


def _table_header(out: str) -> list[str]:
    return [cell.strip() for cell in out.splitlines()[1].strip("│").split("│")]


def test_run_table_of_passing_cases_leaves_out_empty_failure_columns(
    cli: CliInvoker, fake_aap: FakeAap, tmp_path: Path
) -> None:
    _seed_jt(fake_aap)

    result = cli.invoke(app, ["test", "run", str(_smoke(tmp_path))])

    assert result.exit_code == 0, result.output
    header = _table_header(result.stdout)
    assert header[:3] == ["suite", "case", "result"]
    assert header[-1] == "job_url"
    assert not any(column.startswith(("failure", "change")) for column in header)


def test_list_table_leaves_out_an_unused_template_column(cli: CliInvoker, tmp_path: Path) -> None:
    result = cli.invoke(app, ["test", "list", str(_smoke(tmp_path))])

    assert result.exit_code == 0, result.output
    assert _table_header(result.stdout) == ["suite", "case", "job_template"]
