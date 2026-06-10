"""CLI help tests for `untaped workspace`."""

from __future__ import annotations

import pytest
from untaped.testing import CliInvoker

from untaped_workspace import app

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_help_lists_all_commands() -> None:
    result = CliInvoker().invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "list",
        "init",
        "adopt",
        "forget",
        "add",
        "remove",
        "sync",
        "status",
        "foreach",
        "import",
        "path",
        "shell-init",
        "edit",
        "show",
        "branch",
    ):
        assert cmd in result.stdout


@pytest.mark.parametrize(
    "cmd",
    [
        "init",
        "adopt",
        "forget",
        "add",
        "remove",
        "foreach",
        "import",
        "path",
        "shell-init",
        "branch",
    ],
)
def test_no_args_shows_help(cmd: str) -> None:
    result = CliInvoker().invoke(app, [cmd])
    assert result.exit_code in (0, 1, 2)


def test_init_no_args_is_usage_error() -> None:
    result = CliInvoker().invoke(app, ["init"])

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "requires an argument" in result.stderr


def test_branch_set_no_args_is_usage_error() -> None:
    result = CliInvoker().invoke(app, ["branch", "set"])

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "BRANCH requires an argument" in result.stderr


def test_stdin_flags_do_not_expose_negative_aliases() -> None:
    for args in (["add", "--help"], ["remove", "--help"], ["path", "--help"]):
        result = CliInvoker().invoke(app, args)

        assert result.exit_code == 0, result.output
        assert "--stdin" in result.output
        assert "--no-stdin" not in result.output
