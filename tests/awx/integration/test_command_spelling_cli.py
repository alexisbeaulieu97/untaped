"""Renamed and removed AWX commands and flags are gone in 8.0: no deprecated aliases."""

from __future__ import annotations

import pytest

from untaped.bootstrap import build_root_app
from untaped.testing import invoke_cli

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "old",
    [
        ["awx", "save", "--all-kinds", "--out-dir", "out"],
        ["awx", "job-templates", "save", "deploy"],
        ["awx", "job-templates", "usage", "deploy", "-r"],
        ["awx", "workflow-templates", "nodes", "flow", "-r"],
        ["awx", "job-templates", "launch", "deploy", "--limit", "web"],
        ["awx", "job-templates", "launch", "deploy", "--track"],
        ["awx", "projects", "sync", "playbooks", "-t"],
        ["awx", "job-templates", "apply", "deploy.yml"],
    ],
)
def test_old_spellings_are_usage_errors(old: list[str]) -> None:
    result = invoke_cli(build_root_app(candidates=[]), old)

    assert result.exit_code == 2, result.output
    assert "deprecated" not in result.stderr


def test_help_lists_only_the_current_spellings() -> None:
    root = build_root_app(candidates=[])

    awx_help = invoke_cli(root, ["awx", "--help"]).stdout
    inventories_help = invoke_cli(root, ["awx", "inventories", "--help"]).stdout
    templates_help = invoke_cli(root, ["awx", "job-templates", "--help"]).stdout

    assert " save " not in awx_help
    assert " export " in awx_help
    assert " apply " in awx_help
    assert "input-inventories" in inventories_help
    assert " apply " not in templates_help
    assert " export " in templates_help


def test_ping_options_are_keyword_only() -> None:
    result = invoke_cli(build_root_app(candidates=[]), ["awx", "ping", "json"])

    assert result.exit_code == 2, result.output
