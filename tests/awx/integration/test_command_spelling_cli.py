"""Renamed AWX commands and flags keep their old spelling as deprecated aliases."""

from __future__ import annotations

import pytest

from untaped.bootstrap import build_root_app
from untaped.testing import invoke_cli

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("old", "new", "usage"),
    [
        (["awx", "save"], "`export`", "untaped awx export"),
        (["awx", "job-templates", "save"], "`export`", "untaped awx job-templates export"),
        (
            ["awx", "inventories", "input_inventories"],
            "`input-inventories`",
            "untaped awx inventories input-inventories",
        ),
        (
            ["awx", "inventories", "instance_groups"],
            "`instance-groups`",
            "untaped awx inventories instance-groups",
        ),
        (["awx", "jobs", "logs", "-f"], "`--follow`", "untaped awx jobs logs"),
        (
            ["awx", "job-templates", "usage", "-r"],
            "`--recursive`",
            "untaped awx job-templates usage",
        ),
        (
            ["awx", "workflow-templates", "nodes", "-r"],
            "`--recursive`",
            "untaped awx workflow-templates nodes",
        ),
        (
            ["awx", "job-templates", "launch", "--limit", "web"],
            "`--host-pattern`",
            "untaped awx job-templates launch",
        ),
    ],
)
def test_old_spelling_warns_and_resolves_to_the_new_one(
    old: list[str], new: str, usage: str
) -> None:
    result = invoke_cli(build_root_app(externals=[]), [*old, "--help"])

    assert result.exit_code == 0, result.output
    assert f"is deprecated and will be removed in 7.0; use {new}" in result.stderr
    assert f"Usage: {usage}" in result.stdout


def test_old_spellings_are_hidden_from_help() -> None:
    root = build_root_app(externals=[])

    awx_help = invoke_cli(root, ["awx", "--help"]).stdout
    inventories_help = invoke_cli(root, ["awx", "inventories", "--help"]).stdout
    logs_help = invoke_cli(root, ["awx", "jobs", "logs", "--help"]).stdout
    launch_help = invoke_cli(root, ["awx", "job-templates", "launch", "--help"]).stdout

    assert " save " not in awx_help
    assert " export " in awx_help
    assert "│ input_inventories" not in inventories_help
    assert "input-inventories" in inventories_help
    assert " -f " not in logs_help
    assert "--limit" not in launch_help
    assert "--host-pattern" in launch_help


def test_ping_options_are_keyword_only() -> None:
    result = invoke_cli(build_root_app(externals=[]), ["awx", "ping", "json"])

    assert result.exit_code == 2, result.output
