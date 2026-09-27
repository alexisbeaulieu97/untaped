"""CLI tests for ``jira.confirm`` and the readable write previews.

``jira.confirm`` (``always`` | ``destructive`` | ``never``) picks which writes
ask first; the preview shows each request as ``field: old → new`` lines,
reading the issue's current values only when a preview is shown.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from untaped import bootstrap
from untaped.capabilities.jira.cli import app
from untaped.capabilities.jira.settings import JiraSettings
from untaped.testing import CliResult, ScriptedPromptBackend, invoke_cli

BASE = "https://jira.example.com"

WRITES = {
    "create": ["issues", "create", "--project", "ABC", "--summary", "x"],
    "comment": ["issues", "comment", "ABC-1", "--body", "hi"],
    "link": ["issues", "links", "create", "ABC-1", "Blocks", "ABC-2"],
    "patch": ["issues", "patch", "ABC-1", "--summary", "x"],
    "transition": ["issues", "transition", "ABC-1", "--id", "31"],
}
DESTRUCTIVE = {"patch", "transition"}
CURRENT = {
    "key": "ABC-1",
    "fields": {
        "summary": "Old title",
        "assignee": {"name": "alice", "displayName": "Alice"},
        "status": {"name": "To Do"},
        "resolution": None,
        "labels": ["infra"],
    },
}
TRANSITIONS = {
    "transitions": [{"id": "31", "name": "Start Progress", "to": {"name": "In Progress"}}]
}


def _set_confirm(config: Path, policy: str) -> None:
    config.write_text(config.read_text() + f"      confirm: {policy}\n")


def _mock_jira(mock: respx.MockRouter) -> tuple[respx.Route, respx.Route]:
    """Mock every read the previews make; return the (issue GET, write) routes."""
    issue = mock.get("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(200, json=CURRENT))
    mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
        return_value=httpx.Response(200, json=TRANSITIONS)
    )
    mock.get("/rest/api/2/myself").mock(return_value=httpx.Response(200, json={"name": "me"}))
    writes = mock.route(method__in=["POST", "PUT"]).mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ABC-1"})
    )
    return issue, writes


def _run(args: list[str], **kwargs: Any) -> tuple[CliResult, respx.Route, respx.Route]:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        issue, writes = _mock_jira(mock)
        result = invoke_cli(app, args, **kwargs)
    return result, issue, writes


# --- jira.confirm ------------------------------------------------------------------


def test_confirm_defaults_to_destructive() -> None:
    assert JiraSettings().confirm == "destructive"
    with pytest.raises(ValidationError):
        JiraSettings.model_validate({"confirm": "sometimes"})


@pytest.mark.parametrize("verb", sorted(WRITES))
def test_default_policy_asks_only_for_destructive_writes(verb: str) -> None:
    result, _, writes = _run(WRITES[verb])

    if verb in DESTRUCTIVE:
        assert result.exit_code == 2, result.output
        assert f"error: {verb} requires --yes when not interactive" in result.stderr
        assert len(writes.calls) == 0
    else:
        assert result.exit_code == 0, result.output
        assert len(writes.calls) == 1


@pytest.mark.parametrize("verb", sorted(WRITES))
def test_always_policy_asks_for_every_write(jira_config: Path, verb: str) -> None:
    _set_confirm(jira_config, "always")
    result, _, writes = _run(WRITES[verb])

    assert result.exit_code == 2, result.output
    assert "requires --yes when not interactive" in result.stderr
    assert len(writes.calls) == 0


@pytest.mark.parametrize("verb", sorted(WRITES))
def test_never_policy_sends_without_asking_or_previewing(jira_config: Path, verb: str) -> None:
    _set_confirm(jira_config, "never")
    result, issue, writes = _run([*WRITES[verb], "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(writes.calls) == 1
    assert len(issue.calls) == 0
    assert "→" not in result.stderr


def test_dry_run_still_previews_under_the_never_policy(jira_config: Path) -> None:
    _set_confirm(jira_config, "never")
    result, _, writes = _run([*WRITES["patch"], "--dry-run"])

    assert result.exit_code == 0, result.output
    assert 'summary: "Old title" → "x"' in result.stderr
    assert len(writes.calls) == 0


def test_additive_patch_is_not_destructive(tmp_path: Path) -> None:
    fields_file = tmp_path / "labels.yml"
    fields_file.write_text("update:\n  labels:\n    - add: urgent\n")
    result, _, writes = _run(["issues", "patch", "ABC-1", "--fields-file", str(fields_file)])

    assert result.exit_code == 0, result.output
    assert len(writes.calls) == 1


@pytest.mark.parametrize("update", ["labels:\n    - remove: infra\n", "labels:\n    - set: [a]\n"])
def test_removing_or_replacing_patch_is_destructive(tmp_path: Path, update: str) -> None:
    fields_file = tmp_path / "labels.yml"
    fields_file.write_text(f"update:\n  {update}")
    result, _, writes = _run(["issues", "patch", "ABC-1", "--fields-file", str(fields_file)])

    assert result.exit_code == 2, result.output
    assert len(writes.calls) == 0


@pytest.mark.parametrize("args", [["--assignee", "bob"], ["--unassign"]])
def test_assignee_changes_are_destructive(args: list[str]) -> None:
    result, _, writes = _run(["issues", "patch", "ABC-1", *args])

    assert result.exit_code == 2, result.output
    assert len(writes.calls) == 0


def test_yes_skips_the_prompt_and_the_preview_reads() -> None:
    result, issue, writes = _run(
        ["issues", "patch", "ABC-1", "--summary", "New", "--assignee", "bob", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert len(writes.calls) == 2
    assert len(issue.calls) == 0


# --- readable previews -----------------------------------------------------------


def test_patch_preview_diffs_current_values() -> None:
    result, issue, writes = _run(
        [
            "issues",
            "patch",
            "ABC-1",
            "--summary",
            "New title",
            "--set-json",
            'labels=["infra"]',
            "--assignee",
            "bob",
            "--dry-run",
            "--format",
            "json",
        ]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        "PUT /rest/api/2/issue/ABC-1",
        '  summary: "Old title" → "New title"',
        '  labels: ["infra"] (unchanged)',
        "PUT /rest/api/2/issue/ABC-1/assignee",
        "  assignee: alice → bob",
    ]
    requested = set(issue.calls[0].request.url.params["fields"].split(","))
    assert requested == {"summary", "labels", "assignee"}
    assert json.loads(result.stdout)["action"] == "planned"
    assert len(writes.calls) == 0


def test_patch_preview_shows_update_operations_and_unassign(tmp_path: Path) -> None:
    fields_file = tmp_path / "edit.yml"
    fields_file.write_text(
        "update:\n  labels:\n    - add: urgent\n    - remove: infra\n    - set: [a, b]\n"
    )
    result, _, _ = _run(
        ["issues", "patch", "ABC-1", "--fields-file", str(fields_file), "--unassign", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        "PUT /rest/api/2/issue/ABC-1",
        '  labels: + "urgent"',
        '  labels: - "infra"',
        '  labels: ["infra"] → ["a", "b"]',
        "PUT /rest/api/2/issue/ABC-1/assignee",
        "  assignee: alice → (none)",
    ]


def test_patch_preview_shortens_long_text() -> None:
    result, _, _ = _run(["issues", "patch", "ABC-1", "--description", "x" * 200, "--dry-run"])

    assert result.exit_code == 0, result.output
    line = result.stderr.splitlines()[1]
    assert line.startswith('  description: (none) → "xxx')
    assert line.endswith('…"')
    assert len(line) < 100


def test_confirmation_prompt_shows_the_readable_preview() -> None:
    backend = ScriptedPromptBackend(confirms=[False])
    result, _, writes = _run(
        ["issues", "patch", "ABC-1", "--summary", "New"], terminal=True, prompt_backend=backend
    )

    assert result.exit_code == 1, result.output
    assert '  summary: "Old title" → "New"' in result.stderr
    assert "cancelled; no changes made" in result.stderr
    assert len(writes.calls) == 0


def test_transition_preview_shows_the_status_change() -> None:
    result, _, _ = _run(
        [
            "issues",
            "transition",
            "ABC-1",
            "--id",
            "31",
            "--resolution",
            "Fixed",
            "--comment",
            "Shipped.",
            "--dry-run",
        ]
    )

    assert result.exit_code == 0, result.output
    lines = result.stderr.splitlines()
    assert lines[lines.index("POST /rest/api/2/issue/ABC-1/transitions") :] == [
        "POST /rest/api/2/issue/ABC-1/transitions",
        "  transition: Start Progress (31)",
        "  status: To Do → In Progress",
        "  resolution: (none) → Fixed",
        '  comment: + "Shipped."',
    ]


def test_create_preview_lists_the_new_values() -> None:
    result, _, _ = _run(
        [
            "issues",
            "create",
            "--project",
            "ABC",
            "--issue-type",
            "Bug",
            "--summary",
            "Fix deploy",
            "--dry-run",
        ]
    )

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        "POST /rest/api/2/issue",
        "  project: ABC",
        "  issuetype: Bug",
        '  summary: "Fix deploy"',
    ]


def test_comment_preview_shows_the_whole_body() -> None:
    result, _, _ = _run(["issues", "comment", "ABC-1", "--dry-run"], input="one\n\ntwo\n")

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        "POST /rest/api/2/issue/ABC-1/comment",
        "  comment:",
        "    one",
        "",
        "    two",
    ]


def test_link_preview_reads_in_words() -> None:
    result, _, _ = _run([*WRITES["link"], "--dry-run"])

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        "POST /rest/api/2/issueLink",
        "  reads as: ABC-1 <outward phrase of 'Blocks'> ABC-2",
    ]


# --- renamed flags and removed aliases ---------------------------------------------


@pytest.mark.parametrize(
    ("args", "old"),
    [
        (["issues", "patch", "ABC-1"], "--body-file"),
        (["issues", "create", "--project", "ABC"], "--template"),
    ],
)
def test_fields_documents_only_come_from_fields_file(
    tmp_path: Path, args: list[str], old: str
) -> None:
    fields_file = tmp_path / "fields.yml"
    fields_file.write_text("fields:\n  summary: From file\n")
    new, _, new_writes = _run([*args, "--fields-file", str(fields_file), "--yes"])
    stale, _, stale_writes = _run([*args, old, str(fields_file), "--yes"])

    assert new.exit_code == 0, new.output
    assert json.loads(new_writes.calls[0].request.content)["fields"]["summary"] == "From file"
    assert stale.exit_code == 2, stale.output
    assert len(stale_writes.calls) == 0


@pytest.mark.parametrize(
    "args",
    [
        ["me"],
        ["issue", "get", "ABC-1"],
        ["project", "list"],
        ["board", "list"],
        ["sprint", "list"],
        ["issues", "edit", "ABC-1", "--summary", "x", "--yes"],
        ["issues", "patch", "ABC-1", "--field", "summary=x", "--yes"],
        ["issues", "create", "--project", "A", "--json-field", "labels=[]", "--yes"],
    ],
)
def test_old_spellings_are_gone(args: list[str]) -> None:
    root = bootstrap.build_root_app(externals=[])
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        result = invoke_cli(root, ["jira", *args])

    assert result.exit_code == 2, result.output
    assert "deprecated" not in result.stderr
    assert len(route.calls) == 0
