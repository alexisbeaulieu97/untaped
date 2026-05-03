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


def test_run_against_missing_file_emits_clean_error(
    cli: CliRunner, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """A typo'd test path should not leak a Python traceback."""
    missing = tmp_path / "does-not-exist.yml"
    result = cli.invoke(app, ["test", "run", str(missing), "--non-interactive"])
    assert result.exit_code != 0
    combined = (result.stderr or "") + (result.output or "")
    assert "Traceback" not in combined
    # ``typer.BadParameter`` renders an "Error" box; the precise text wraps
    # depending on terminal width, so we only assert the user-visible signal.
    assert "Error" in combined


def test_run_with_broken_vars_file_emits_clean_error(
    cli: CliRunner, fake_aap: FakeAap, tmp_path: Path
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


def test_run_against_directory_picks_up_yaml_children(
    cli: CliRunner, fake_aap: FakeAap, tmp_path: Path
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


def test_fake_aap_next_action_status_is_one_shot(
    cli: CliRunner, fake_aap: FakeAap, tmp_path: Path
) -> None:
    """Setting ``next_action_status`` once must not bleed into a second launch."""
    _seed_jt(fake_aap)
    fake_aap.next_action_status = "failed"

    test_file = _write(
        tmp_path / "matrix.yml",
        "kind: AwxTestSuite\nname: m\njobTemplate: Deploy app\n"
        "cases:\n  first:\n    launch: {}\n  second:\n    launch: {}\n",
    )

    result = cli.invoke(app, ["test", "run", str(test_file), "--non-interactive"])

    assert result.exit_code == 1  # one of the cases failed
    out = result.stdout
    # First case picks up the override (failed); second resets to successful.
    assert "fail" in out
    assert "pass" in out


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


def test_list_json_includes_variable_metadata(
    cli: CliRunner, fake_aap: FakeAap, tmp_path: Path
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
