"""CLI help tests for `untaped workspace`."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from untaped_workspace import app

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_help_lists_all_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
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
    result = CliRunner().invoke(app, [cmd])
    # no_args_is_help: exit 0 (help) or 2 (Click's missing arg)
    assert result.exit_code in (0, 2)


def test_branch_set_no_args_shows_help() -> None:
    result = CliRunner().invoke(app, ["branch", "set"])
    assert result.exit_code in (0, 2)
    assert "Usage:" in result.stdout
