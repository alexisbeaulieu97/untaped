"""CLI tests for the 8.0 workspace surface: the ``repos`` noun and positional WS.

The workspace is the first positional argument of every command that acts on
one: a registered name, or a path inside a workspace (``.`` is the current
directory). Omitting it targets the workspace containing the current directory,
except on ``repos add`` / ``repos remove``, whose repos follow it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped import bootstrap
from untaped.capabilities.workspace import SPEC
from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("isolate_config")


def _init(runner: CliInvoker, tmp_path: Path, name: str = "prod") -> Path:
    target = tmp_path / name
    result = runner.invoke(app, ["init", name, "--path", str(target)])
    assert result.exit_code == 0, result.output
    return target.resolve()


def _repos(runner: CliInvoker, *args: str) -> list[str]:
    result = runner.invoke(app, ["repos", "list", *args, "--format", "raw", "--columns", "repo"])
    assert result.exit_code == 0, result.output
    return result.stdout.splitlines()


def test_repos_add_list_remove_take_the_workspace_first(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    added = runner.invoke(app, ["repos", "add", "prod", "https://x/api.git", "https://x/ui.git"])
    assert added.exit_code == 0, added.output
    assert _repos(runner, "prod") == ["api", "ui"]

    removed = runner.invoke(app, ["repos", "remove", "prod", "ui"])
    assert removed.exit_code == 0, removed.output
    assert _repos(runner, "prod") == ["api"]


def test_repos_list_emits_repo_records(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = _init(runner, tmp_path)
    runner.invoke(app, ["repos", "add", "prod", "https://x/api.git"])

    result = runner.invoke(app, ["repos", "list", "prod", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["kind"] == "workspace.repo"
    assert envelope["record"]["repo"] == "api"
    assert envelope["record"]["target_path"] == str(target / "api")


def test_workspace_argument_defaults_to_the_current_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliInvoker()
    target = _init(runner, tmp_path)
    runner.invoke(app, ["repos", "add", "prod", "https://x/api.git"])
    (target / "nested").mkdir()
    monkeypatch.chdir(target / "nested")

    assert _repos(runner) == ["api"]
    assert _repos(runner, ".") == ["api"]


def test_workspace_argument_accepts_a_path(tmp_path: Path) -> None:
    runner = CliInvoker()
    target = _init(runner, tmp_path)
    runner.invoke(app, ["repos", "add", str(target), "https://x/api.git"])

    assert _repos(runner, str(target)) == ["api"]
    assert _repos(runner, "prod") == ["api"]


def test_workspace_path_outside_any_workspace_is_an_error(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["repos", "list", str(tmp_path)])

    assert result.exit_code == 1
    assert f"no workspace manifest at or above {tmp_path.resolve()}" in result.stderr


def test_repos_add_with_stdin_accepts_an_omitted_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliInvoker()
    target = _init(runner, tmp_path)
    monkeypatch.chdir(target)

    result = runner.invoke(app, ["repos", "add", "--stdin"], input="https://x/api.git\n")

    assert result.exit_code == 0, result.output
    assert _repos(runner) == ["api"]


def test_repos_add_single_positional_needs_a_url(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    result = runner.invoke(app, ["repos", "add", "https://x/api.git"])

    assert result.exit_code == 2
    assert "missing URL (or --stdin)" in result.stderr
    assert _repos(runner, "prod") == []


def test_repos_remove_single_positional_names_the_workspace_first(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    result = runner.invoke(app, ["repos", "remove", "api"])

    assert result.exit_code == 2
    assert "missing REPO (or --stdin)" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["branch", "set", "main"],
        ["branch", "set", "prod", "main"],
    ],
)
def test_branch_set_takes_an_optional_leading_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    runner = CliInvoker()
    target = _init(runner, tmp_path)
    monkeypatch.chdir(target)

    result = runner.invoke(app, [*args, "--format", "json"])

    assert result.exit_code == 0, result.output
    assert "branch: main" in (target / "untaped.yml").read_text()


def test_branch_set_rejects_extra_positionals(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    result = runner.invoke(app, ["branch", "set", "prod", "main", "extra"])

    assert result.exit_code == 2


def test_workspace_option_spelling_is_gone(tmp_path: Path) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    for args in (["status", "--workspace", "prod"], ["sync", "-w", "prod"], ["edit", "-p", "."]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, (args, result.output)


def test_help_shows_repos_noun_and_no_top_level_repo_verbs() -> None:
    result = CliInvoker().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    commands = {line.split()[1] for line in result.stdout.splitlines() if line.startswith("│ ")}
    assert "repos" in commands
    assert not {"add", "remove", "get", "show"} & commands


@pytest.fixture
def _root_isolation() -> object:
    bootstrap._clear_for_tests()
    yield
    bootstrap._clear_for_tests()


@pytest.mark.usefixtures("_root_isolation")
@pytest.mark.parametrize("old", [["show"], ["get"], ["add", "https://x/a.git"], ["remove", "a"]])
def test_old_spellings_have_no_aliases(tmp_path: Path, old: list[str]) -> None:
    root = bootstrap.build_root_app(builtins=(SPEC,), externals=())
    runner = CliInvoker()
    runner.invoke(root.meta, ["workspace", "init", "prod", "--path", str(tmp_path / "ws")])

    result = runner.invoke(root.meta, ["workspace", *old])

    assert result.exit_code == 2, result.output
    assert "deprecated" not in result.stderr


def test_unknown_user_home_is_a_clean_error() -> None:
    result = CliInvoker().invoke(app, ["status", "~nosuchuser-untaped"])

    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")
    assert "~nosuchuser-untaped" in result.stderr


def test_missing_workspace_path_does_not_fall_back_to_the_enclosing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliInvoker()
    target = _init(runner, tmp_path)
    monkeypatch.chdir(target)

    result = runner.invoke(app, ["repos", "list", "./nonexistent/dir"])

    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert result.stdout == ""


def test_init_rejects_names_that_look_like_a_home_path(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["init", "~x", "--path", str(tmp_path / "ws")])

    assert result.exit_code == 2  # a bad NAME argument is a usage error
    assert "'~x'" in result.stderr
    assert not (tmp_path / "ws").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["repos", "add", "prod"], "missing URL (or --stdin)"),
        (["repos", "remove", "prod"], "missing REPO (or --stdin)"),
    ],
)
def test_repos_mutations_name_the_missing_argument(
    tmp_path: Path, args: list[str], message: str
) -> None:
    runner = CliInvoker()
    _init(runner, tmp_path)

    result = runner.invoke(app, args)

    assert result.exit_code == 2
    assert message in result.stderr
