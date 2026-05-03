"""End-to-end CLI tests for ``awx test`` (run, list, validate)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner
from untaped_awx import app

if TYPE_CHECKING:  # pragma: no cover — pytest --import-mode=importlib hides 'tests'
    from tests.conftest import FakeAap
else:
    FakeAap = object  # type: ignore[assignment,misc]


@pytest.fixture
def cli() -> CliRunner:
    return CliRunner()


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
    fake.seed("job_templates", name=name)


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_test_help_lists_subcommands(cli: CliRunner) -> None:
    result = cli.invoke(app, ["test", "--help"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "run" in out
    assert "list" in out
    assert "validate" in out


def test_run_passes_when_job_succeeds(cli: CliRunner, fake_aap: FakeAap, tmp_path: Path) -> None:
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


def test_run_filters_to_one_case(cli: CliRunner, fake_aap: FakeAap, tmp_path: Path) -> None:
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
    cli: CliRunner, fake_aap: FakeAap, tmp_path: Path
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


def test_run_uses_var_flag(cli: CliRunner, fake_aap: FakeAap, tmp_path: Path) -> None:
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
    cli: CliRunner, fake_aap: FakeAap, tmp_path: Path
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
    cli: CliRunner, fake_aap: FakeAap, tmp_path: Path
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


def test_list_dumps_cases_in_json(cli: CliRunner, fake_aap: FakeAap, tmp_path: Path) -> None:
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
