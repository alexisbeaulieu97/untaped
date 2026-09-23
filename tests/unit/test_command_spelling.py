"""Loose command spellings resolve to the registered command, help included."""

from __future__ import annotations

from untaped.bootstrap import build_root_app
from untaped.testing import invoke_cli


def test_underscore_command_spelling_renders_help_instead_of_crashing() -> None:
    root = build_root_app(externals=[])
    result = invoke_cli(root, ["awx", "job_templates", "--help"])

    assert result.exit_code == 0, result.output
    assert "Usage: untaped awx job-templates" in result.stdout


def test_canonical_spelling_leaves_arguments_and_options_alone() -> None:
    root = build_root_app(externals=[])
    result = invoke_cli(root, ["awx", "JobTemplates", "list", "--help"])

    assert result.exit_code == 0, result.output
    assert "Usage: untaped awx job-templates list" in result.stdout
