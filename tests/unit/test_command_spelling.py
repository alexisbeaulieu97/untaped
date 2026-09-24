"""Loose command spellings resolve to the registered command, help included."""

from __future__ import annotations

import pytest
from cyclopts import App

from untaped._root_options import canonical_command_tokens
from untaped.bootstrap import build_root_app
from untaped.cli import deprecated_alias
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


def _toy_root() -> tuple[App, list[tuple[str, bool]]]:
    calls: list[tuple[str, bool]] = []
    root = App(name="untaped")
    jira = App(name="jira")
    root.command(jira)

    @jira.command(name="whoami")
    def whoami() -> None:
        calls.append(("whoami", False))

    logs = App(name="logs")
    jira.command(logs)

    @logs.default
    def logs_command(*, follow: bool = False) -> None:
        calls.append(("logs", follow))

    deprecated_alias(jira, "me", "whoami")
    deprecated_alias(logs, "-F", "--follow")
    return root, calls


def test_deprecated_command_alias_is_rewritten_with_a_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = _toy_root()

    assert canonical_command_tokens(root, ["jira", "me"]) == ["jira", "whoami"]
    assert "warning: `me` is deprecated and will be removed in 8.0; use `whoami`" in (
        capsys.readouterr().err
    )
    assert "me" not in list(root["jira"])  # hidden: never listed in help


def test_deprecated_option_alias_is_rewritten_up_to_the_separator(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ = _toy_root()

    assert canonical_command_tokens(root, ["jira", "logs", "-F", "--", "-F"]) == [
        "jira",
        "logs",
        "--follow",
        "--",
        "-F",
    ]
    assert "use `--follow`" in capsys.readouterr().err
    assert canonical_command_tokens(root, ["jira", "logs"]) == ["jira", "logs"]


def test_alias_must_not_mix_commands_and_options() -> None:
    with pytest.raises(ValueError, match="mixes a command and an option"):
        deprecated_alias(App(name="x"), "old", "--new")
