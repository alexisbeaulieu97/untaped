"""`workspace run` end to end with real repos."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from untaped.capabilities.workspace.cli import app
from untaped.capabilities.workspace.cli.common import run_argv
from untaped.testing import CliInvoker

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("workspace_env")]
run = CliInvoker().invoke


@pytest.fixture
def ws(make_upstream: Callable[..., Path], workspace_env: Path) -> Path:
    result = run(
        app,
        [
            "create",
            "J-1",
            "--repo",
            str(make_upstream("api")),
            "--read-only",
            str(make_upstream("docs")),
        ],
    )
    assert result.exit_code == 0, result.output
    return workspace_env / "J-1"


def _rows(result) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    return json.loads(result.stdout)


def test_command_string_with_a_pipe(ws: Path) -> None:
    result = run(app, ["run", "J-1", "git log --oneline | wc -l", "--format", "json"])
    assert result.exit_code == 0, result.output
    [row] = _rows(result)  # read-only docs is excluded by default
    assert (row["repo"], row["stdout"].strip()) == ("acme/api", "1")


def test_include_read_only(ws: Path) -> None:
    rows = _rows(run(app, ["run", "J-1", "true", "--include-read-only", "--format", "json"]))
    assert sorted(r["dir"] for r in rows) == ["api", "docs"]


def test_name_from_cwd(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ws / "api")
    assert run(app, ["run", "true"]).exit_code == 0


def test_relative_script_resolves_against_the_caller(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "s.sh").write_text('echo "$UNTAPED_REPO"\n')
    monkeypatch.chdir(tmp_path)
    rows = _rows(run(app, ["run", "J-1", "s.sh", "--format", "json"]))
    assert rows[0]["stdout"].strip() == "acme/api"


def test_stdin_script(ws: Path) -> None:
    rows = _rows(
        run(app, ["run", "J-1", "-", "--format", "json"], input='echo "$UNTAPED_BRANCH"\n')
    )
    assert rows[0]["stdout"].strip() == "J-1"


def test_stdin_script_with_stdin_selection_is_usage(ws: Path) -> None:
    assert run(app, ["run", "J-1", "-", "--stdin"], input="true\n").exit_code == 2


def test_failure_exits_one_and_human_summary(ws: Path) -> None:
    result = run(app, ["run", "J-1", "exit 4", "--include-read-only"])
    assert result.exit_code == 1
    assert "── acme/api (api) · exit 4 ──" in result.stdout
    assert "failed" in result.stderr


def test_unknown_repo_selector(ws: Path) -> None:
    result = run(app, ["run", "J-1", "true", "--repo", "nope"])
    assert result.exit_code == 2
    assert "api" in result.output


@pytest.fixture
def two(make_upstream: Callable[..., Path], workspace_env: Path) -> Path:
    result = run(
        app,
        ["create", "J-2", "--repo", str(make_upstream("api")), "--repo", str(make_upstream("web"))],
    )
    assert result.exit_code == 0, result.output
    return workspace_env / "J-2"


def test_script_without_shebang_runs_with_sh(ws: Path, tmp_path: Path) -> None:
    script = tmp_path / "plain.sh"
    script.write_text('echo "$UNTAPED_REPO"\n')
    script.chmod(0o755)
    rows = _rows(run(app, ["run", "J-1", str(script), "--format", "json"]))
    assert rows[0]["stdout"].strip() == "acme/api"


def test_executable_script_with_shebang_runs_directly(ws: Path, tmp_path: Path) -> None:
    script = tmp_path / "direct.sh"
    script.write_text("#!/bin/sh\necho direct\n")
    script.chmod(0o755)
    rows = _rows(run(app, ["run", "J-1", str(script), "--format", "json"]))
    assert rows[0]["stdout"].strip() == "direct"


def test_timeout_reason_in_human_output(ws: Path) -> None:
    result = run(app, ["run", "J-1", "sleep 5", "--timeout", "0.5"])
    assert result.exit_code == 1
    assert "── acme/api (api) · timed out after 0.5s ──" in result.stdout


def test_fail_fast_skip_prints_no_block(two: Path) -> None:
    result = run(app, ["run", "J-2", "exit 1", "--fail-fast", "--parallel", "1"])
    assert result.exit_code == 1
    assert result.stdout.count("──") == 2  # one header only
    assert "1 skipped" in result.stderr


def test_command_starting_with_hyphen(ws: Path) -> None:
    result = run(app, ["run", "J-1", "--format", "json", "--", "-x"])
    assert result.exit_code == 1
    stderr = str(_rows(result)[0]["stderr"])
    assert "-x" in stderr
    assert "requires an argument" not in stderr


def test_dash_with_nothing_piped_is_usage(ws: Path) -> None:
    result = run(app, ["run", "J-1", "-"], input="")
    assert result.exit_code == 2
    assert "nothing was piped" in result.output


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_timeout_must_be_positive_and_finite(ws: Path, value: str) -> None:
    assert run(app, ["run", "J-1", "true", "--timeout", value]).exit_code == 2


def test_read_only_repo_needs_include_flag(ws: Path) -> None:
    result = run(app, ["run", "J-1", "true", "--repo", "docs"])
    assert result.exit_code == 2
    assert "--include-read-only" in result.output


def test_stdin_selection(two: Path) -> None:
    rows = _rows(run(app, ["run", "J-2", "true", "--stdin", "--format", "json"], input="web\n"))
    assert [r["dir"] for r in rows] == ["web"]


def test_repo_by_display_name(two: Path) -> None:
    rows = _rows(run(app, ["run", "J-2", "true", "--repo", "acme/web", "--format", "json"]))
    assert [r["repo"] for r in rows] == ["acme/web"]


def test_option_before_positionals(ws: Path) -> None:
    rows = _rows(run(app, ["run", "--format", "json", "J-1", "true"]))
    assert [r["dir"] for r in rows] == ["api"]


def test_json_mode_keeps_summary_off_stdout(ws: Path) -> None:
    result = run(app, ["run", "J-1", "true", "--format", "json"])
    assert " ok" not in result.stdout
    assert "──" not in result.stdout


def _pipe(*args: str) -> str:
    result = run(app, [*args, "--format", "pipe"])
    assert result.exit_code == 0, result.output
    return result.stdout


def test_stdin_status_rows_skip_read_only_repos(ws: Path) -> None:
    piped = _pipe("status", "J-1")
    result = run(app, ["run", "J-1", "true", "--stdin", "--format", "json"], input=piped)
    assert result.exit_code == 0, result.output
    assert [r["dir"] for r in _rows(result)] == ["api"]


def test_stdin_status_rows_with_include_read_only(ws: Path) -> None:
    piped = _pipe("status", "J-1")
    rows = _rows(
        run(
            app,
            ["run", "J-1", "true", "--stdin", "--include-read-only", "--format", "json"],
            input=piped,
        )
    )
    assert sorted(r["dir"] for r in rows) == ["api", "docs"]


def test_empty_pipe_runs_nothing(ws: Path) -> None:
    result = run(app, ["run", "J-1", "touch RAN", "--stdin"], input="")
    assert result.exit_code == 0, result.output
    assert not (ws / "api" / "RAN").exists()


def test_stdin_only_read_only_runs_nothing(ws: Path) -> None:
    result = run(app, ["run", "J-1", "touch RAN", "--stdin", "--format", "json"], input="docs\n")
    assert result.exit_code == 0, result.output
    assert _rows(result) == []
    assert not (ws / "docs" / "RAN").exists()


def test_no_writable_repos_warns_and_exits_zero(
    make_upstream: Callable[..., Path], workspace_env: Path
) -> None:
    created = run(app, ["create", "J-3", "--read-only", str(make_upstream("docs"))])
    assert created.exit_code == 0, created.output
    result = run(app, ["run", "J-3", "true"])
    assert result.exit_code == 0, result.output
    assert "no repos to run in (read-only repos need --include-read-only)" in result.stderr
    assert "──" not in result.stdout


def test_stdin_pipe_envelope_records(two: Path) -> None:
    piped = _pipe("status", "J-2")
    web_only = "".join(line + "\n" for line in piped.splitlines() if '"web"' in line)
    rows = _rows(run(app, ["run", "J-2", "true", "--stdin", "--format", "json"], input=web_only))
    assert [r["dir"] for r in rows] == ["web"]


def _envelope(template: str, record: dict[str, object]) -> str:
    envelope = json.loads(template.splitlines()[0])
    envelope["record"] = record
    return json.dumps(envelope) + "\n"


def test_stdin_records_from_another_workspace_are_ignored(two: Path) -> None:
    piped = _pipe("status", "J-2")
    lines = (
        _envelope(piped, {"workspace": "OTHER", "repo": "acme/api"})
        + _envelope(piped, {"workspace": "J-2", "repo": "acme/web"})
        + _envelope(piped, {"workspace": "OTHER", "dir": "nope"})
    )
    result = run(app, ["run", "J-2", "true", "--stdin", "--format", "json"], input=lines)
    assert result.exit_code == 0, result.output
    assert [r["dir"] for r in _rows(result)] == ["web"]


def test_stdin_record_without_repo_or_dir_is_usage(two: Path) -> None:
    line = _envelope(_pipe("status", "J-2"), {"workspace": "J-2"})
    result = run(app, ["run", "J-2", "true", "--stdin"], input=line)
    assert result.exit_code == 2
    assert "record has no repo or dir" in result.output


def test_stdin_script_temp_file_is_removed(ws: Path) -> None:
    rows = _rows(run(app, ["run", "J-1", "-", "--format", "json"], input='echo "$0"\n'))
    script = Path(str(rows[0]["stdout"]).strip())
    assert script.name.endswith(".sh")
    assert not script.exists()


def test_signal_death_names_the_signal(ws: Path) -> None:
    result = run(app, ["run", "J-1", "kill -TERM $$"])
    assert result.exit_code == 1
    assert "── acme/api (api) · killed by SIGTERM ──" in result.stdout
    rows = _rows(run(app, ["run", "J-1", "kill -TERM $$", "--format", "json"]))
    assert (rows[0]["returncode"], rows[0]["detail"]) == (-15, "killed by SIGTERM")


def test_background_holder_does_not_hold_the_run(ws: Path) -> None:
    result = run(
        app, ["run", "J-1", "sleep 30 & echo started", "--timeout", "20", "--format", "json"]
    )
    [row] = _rows(result)
    assert (row["action"], row["returncode"]) == ("ran", 0)
    assert "started" in str(row["stdout"])
    assert float(row["duration_s"]) < 15  # not held until the 20s timeout


def test_command_after_name_and_double_dash(ws: Path) -> None:
    rows = _rows(run(app, ["run", "J-1", "--format", "json", "--", "-x"]))
    assert rows[0]["action"] == "failed"


def test_mistyped_long_option_as_command_is_usage(ws: Path) -> None:
    result = run(app, ["run", "J-1", "--formt"])
    assert result.exit_code == 2


def test_help_names_the_positionals() -> None:
    result = run(app, ["run", "--help"])
    assert "run [OPTIONS] [NAME] CMD" in result.stdout
    assert "NAME_OR_CMD" in result.stdout


def test_unknown_user_tilde_is_a_command_string() -> None:
    command = "~no_such_user_untaped/x"
    with run_argv(command) as argv:
        assert argv == ["sh", "-c", "--", command]
