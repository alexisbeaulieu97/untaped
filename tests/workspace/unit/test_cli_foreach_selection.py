"""CLI tests for ``workspace foreach`` selection: ``[WS] CMD``, ``--all``, ``--stdin``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("isolate_config")


def _pipe(kind: str, **record: object) -> str:
    return json.dumps({"untaped": "1", "kind": kind, "record": record}) + "\n"


def _workspace(runner: CliInvoker, tmp_path: Path, name: str, repos: tuple[str, ...]) -> Path:
    """A workspace whose declared repos exist as plain directories."""
    target = tmp_path / name
    runner.invoke(app, ["init", name, "--path", str(target)])
    for repo in repos:
        runner.invoke(app, ["repos", "add", name, f"https://x/{repo}.git"])
        (target / repo).mkdir()
    return target


def _ran(result_stdout: str) -> list[tuple[str, str]]:
    return [(row["workspace"], row["repo"]) for row in json.loads(result_stdout)]


def test_foreach_command_alone_uses_the_current_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliInvoker()
    target = _workspace(runner, tmp_path, "prod", ("api", "ui"))
    monkeypatch.chdir(target / "api")

    result = runner.invoke(app, ["foreach", "true", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert _ran(result.stdout) == [("prod", "api"), ("prod", "ui")]


def test_foreach_all_runs_in_every_workspace(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api",))
    _workspace(runner, tmp_path, "lab", ("ui", "db"))

    result = runner.invoke(app, ["foreach", "--all", "true", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert _ran(result.stdout) == [("prod", "api"), ("lab", "ui"), ("lab", "db")]


def test_foreach_all_names_repos_with_their_workspace(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api",))
    _workspace(runner, tmp_path, "lab", ("api",))

    result = runner.invoke(
        app, ["foreach", "--all", "echo hi; false", "--continue-on-error", "-j", "1"]
    )

    assert result.exit_code == 1
    assert result.stdout.splitlines() == ["[prod/api] hi", "[lab/api] hi"]
    assert "failed in: prod/api, lab/api" in result.stderr


def test_foreach_all_skips_unreadable_manifest_with_warning(tmp_path: Path) -> None:
    runner = CliInvoker()
    broken = _workspace(runner, tmp_path, "broken", ())
    _workspace(runner, tmp_path, "lab", ("ui",))
    (broken / "untaped.yml").write_text("repos: [\n")

    result = runner.invoke(app, ["foreach", "--all", "true", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert _ran(result.stdout) == [("lab", "ui")]
    assert "warning: skipped workspace 'broken'" in result.stderr


def test_foreach_stdin_selects_repo_names(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api", "ui", "db"))

    result = runner.invoke(
        app, ["foreach", "prod", "true", "--stdin", "--format", "json"], input="db\napi\n"
    )

    assert result.exit_code == 0, result.output
    assert _ran(result.stdout) == [("prod", "api"), ("prod", "db")]


def test_foreach_stdin_reads_status_records(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api", "ui"))
    piped = _pipe("workspace.status", workspace="prod", repo="ui")

    result = runner.invoke(
        app, ["foreach", "prod", "true", "--stdin", "--format", "json"], input=piped
    )

    assert result.exit_code == 0, result.output
    assert _ran(result.stdout) == [("prod", "ui")]


def test_foreach_stdin_rejects_other_record_kinds(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api",))

    result = runner.invoke(
        app,
        ["foreach", "prod", "true", "--stdin"],
        input=_pipe("workspace.workspace", name="prod", path=str(tmp_path)),
    )

    assert result.exit_code == 2


@pytest.mark.parametrize(
    "args",
    [
        ["--all", "--stdin"],
        ["--stdin", "--repo", "api"],
    ],
)
def test_foreach_selection_modes_are_exclusive(tmp_path: Path, args: list[str]) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api",))

    result = runner.invoke(app, ["foreach", "true", *args], input="api\n")

    assert result.exit_code == 2
    assert result.stdout == ""


def test_foreach_all_rejects_a_workspace_argument(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api",))

    result = runner.invoke(app, ["foreach", "prod", "true", "--all"])

    assert result.exit_code == 2
    assert "--all cannot be combined with a workspace argument" in result.stderr


def test_foreach_requires_a_command() -> None:
    result = CliInvoker().invoke(app, ["foreach", "--all"])

    assert result.exit_code == 2
    assert "missing argument CMD" in result.stderr


def test_foreach_stdin_rejects_records_of_another_workspace(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api",))
    _workspace(runner, tmp_path, "lab", ("api",))
    piped = _pipe("workspace.status", workspace="prod", repo="api")

    result = runner.invoke(app, ["foreach", "lab", "true", "--stdin"], input=piped)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "'prod'" in result.stderr and "'lab'" in result.stderr


def test_foreach_all_repo_filters_per_workspace(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api", "ui"))
    _workspace(runner, tmp_path, "lab", ("db",))
    _workspace(runner, tmp_path, "edge", ("api",))

    result = runner.invoke(app, ["foreach", "--all", "true", "--repo", "api", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert _ran(result.stdout) == [("prod", "api"), ("edge", "api")]


def test_foreach_all_repo_matching_no_workspace_is_an_error(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api",))

    result = runner.invoke(app, ["foreach", "--all", "true", "--repo", "nope"])

    assert result.exit_code == 1
    assert "nope" in result.stderr
    assert result.stdout == ""


def test_foreach_all_fail_fast_stops_before_the_next_workspace(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api",))
    _workspace(runner, tmp_path, "lab", ("ui",))

    result = runner.invoke(app, ["foreach", "--all", "false", "-j", "1", "--format", "json"])

    assert result.exit_code == 1
    assert _ran(result.stdout) == [("prod", "api")]


def test_foreach_unknown_workspace_hints_to_quote_the_command(tmp_path: Path) -> None:
    runner = CliInvoker()
    _workspace(runner, tmp_path, "prod", ("api",))

    result = runner.invoke(app, ["foreach", "build", "make"])

    assert result.exit_code == 1
    assert "unknown workspace: 'build'" in result.stderr
    assert "quote" in result.stderr
