"""Command spellings: loose ones resolve, deprecated ones warn, removed ones are usage errors."""

from __future__ import annotations

import pytest
import respx
from cyclopts import App

from untaped._root_options import canonical_command_tokens
from untaped.bootstrap import build_root_app
from untaped.capabilities.registry import ProviderCandidate
from untaped.cli import deprecated_alias
from untaped.testing import invoke_cli


def test_underscore_command_spelling_renders_help_instead_of_crashing(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    result = invoke_cli(root, ["awx", "job_templates", "--help"])

    assert result.exit_code == 0, result.output
    assert "Usage: untaped awx job-templates" in result.stdout


def test_canonical_spelling_leaves_arguments_and_options_alone(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = build_root_app(candidates=first_party_candidates)
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
    assert (
        "warning: `me` is deprecated and will be removed in the next major release; use `whoami`"
        in (capsys.readouterr().err)
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


#: Spellings removed in a major release, with no deprecated alias left behind.
REMOVED_SPELLINGS = [
    ["ansible", "alias", "list"],
    ["ansible", "source-alias", "add", "common", "acme/common"],
    ["ansible", "source", "save", "prod", "--repo", "acme/site"],
    ["ansible", "source", "edit", "prod", "--add-repo", "acme/api"],
    ["ansible", "source", "show", "prod"],
    ["ansible", "source", "refresh", "prod", "--concurrency", "4"],
    ["ansible", "graph", "acme/site", "--concurrency", "4"],
    ["ansible", "graph", "acme/site", "--output", "graph.json"],
    ["ansible", "graph", "acme/site", "--contains", "acme/base"],
    ["ansible", "graph", "--stdin"],
    ["ansible", "graph", "acme/site", "--upstream"],
    ["ansible", "graph", "acme/site", "--downstream"],
    ["ansible", "graph", "acme/site", "--both"],
    ["awx", "save", "--all-kinds", "--out-dir", "out"],
    ["awx", "export", "--all", "--out-dir", "out"],
    ["awx", "apply", "--file", "x.yml"],
    ["awx", "apply", "-f", "x.yml"],
    ["awx", "job-templates", "save", "deploy"],
    ["awx", "job-templates", "usage", "deploy", "-r"],
    ["awx", "workflow-templates", "nodes", "flow", "-r"],
    ["awx", "job-templates", "launch", "deploy", "--limit", "web"],
    ["awx", "job-templates", "launch", "deploy", "--track"],
    ["awx", "job-templates", "apply", "deploy.yml"],
    ["awx", "projects", "sync", "playbooks", "-t"],
    ["awx", "projects", "update", "playbooks"],
    ["github", "search", "code", "TODO", "--repo-stdin"],
    ["github", "search", "repos", "--no-archived"],
    ["github", "repos", "list", "--org", "acme", "--no-archived"],
    ["github", "repos", "list", "--org", "acme", "--archived"],
    ["github", "repos", "list", "--org", "acme", "--archived", "yes"],
    ["github", "sweep", "--org", "acme", "--grep", "x", "-w"],
    ["github", "sweep", "--org", "acme", "--grep", "x", "--sync"],
    ["github", "sweep", "--org", "acme", "--grep", "x", "--no-sync"],
    ["github", "sweep", "--org", "acme", "--grep", "x", "--archived"],
    ["github", "sweep", "--org", "acme", "--grep", "x", "--archived", "yes"],
    ["jira", "me"],
    ["jira", "issue", "get", "ABC-1"],
    ["jira", "project", "list"],
    ["jira", "board", "list"],
    ["jira", "sprint", "list"],
    ["jira", "issues", "edit", "ABC-1", "--summary", "x", "--yes"],
    ["jira", "issues", "patch", "ABC-1", "--field", "summary=x", "--yes"],
    ["jira", "issues", "create", "--project", "A", "--json-field", "labels=[]", "--yes"],
    ["recipe", "add", "./pack"],
    ["recipe", "sync", "--all"],
    ["recipe", "remove", "acme", "--yes"],
    ["recipe", "hook", "run", "yaml_edit"],
    ["recipe", "backup", "list"],
    ["recipe", "check"],
    ["recipe", "show", "yaml_edit"],
    ["recipe", "new", "pack", "demo"],
    ["recipe", "backups", "show", "latest"],
    ["recipe", "list", "--packs"],
    ["recipe", "list", "--hooks"],
    ["recipe", "init", "pack", "demo"],
    ["recipe", "apply", "r.yml", ".", "--vars", "v.yml"],
    ["recipe", "apply", "r.yml", ".", "--interactive"],
]


@pytest.mark.parametrize("argv", REMOVED_SPELLINGS, ids=" ".join)
def test_removed_spelling_is_a_usage_error_without_an_alias(
    first_party_candidates: tuple[ProviderCandidate, ...], argv: list[str]
) -> None:
    root = build_root_app(candidates=first_party_candidates)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().respond(200, json={})
        result = invoke_cli(root, argv, input="acme/site\n")

    assert result.exit_code == 2, result.output
    assert "deprecated" not in result.stderr
    assert len(route.calls) == 0
