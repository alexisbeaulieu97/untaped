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
        "priority": {"name": "High", "id": "2"},
        "components": [{"name": "API", "id": "10"}],
        "description": "a" * 70 + "x",
    },
}
TRANSITIONS = {
    "transitions": [{"id": "31", "name": "Start Progress", "to": {"name": "In Progress"}}]
}


def _set_confirm(config: Path, policy: str) -> None:
    config.write_text(config.read_text() + f"      confirm: {policy}\n")


def _issue_response(request: httpx.Request) -> httpx.Response:
    """The issue, with its transitions only when ``expand=transitions`` asks for them."""
    body: dict[str, Any] = dict(CURRENT)
    if "transitions" in request.url.params.get("expand", ""):
        body["transitions"] = TRANSITIONS["transitions"]
    return httpx.Response(200, json=body)


def _preview(stderr: str, header: str) -> list[str]:
    """The preview lines from ``header`` on (a progress line may come first)."""
    lines = stderr.splitlines()
    return lines[lines.index(header) :]


def _mock_jira(mock: respx.MockRouter) -> tuple[respx.Route, respx.Route]:
    """Mock every read the previews make; return the (issue GET, write) routes."""
    issue = mock.get("/rest/api/2/issue/ABC-1").mock(side_effect=_issue_response)
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
    assert _preview(result.stderr, "PUT /rest/api/2/issue/ABC-1") == [
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
    assert _preview(result.stderr, "PUT /rest/api/2/issue/ABC-1") == [
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
    line = _preview(result.stderr, "PUT /rest/api/2/issue/ABC-1")[1]
    assert line.startswith('  description: "aaa')
    assert '…" → "xxx' in line
    assert line.endswith('…"')
    assert len(line) < 150  # both sides cut to 60 characters


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
    assert _preview(result.stderr, "POST /rest/api/2/issue/ABC-1/transitions") == [
        "POST /rest/api/2/issue/ABC-1/transitions",
        "  transition: Start Progress (31)",
        "  status: To Do → In Progress",
        "  resolution: (none) → Fixed",
        "  comment:",
        "    Shipped.",
    ]


def test_patch_preview_compares_whole_values_but_shows_them_short() -> None:
    new = "a" * 70 + "y"
    result, _, _ = _run(["issues", "patch", "ABC-1", "--description", new, "--dry-run"])

    assert result.exit_code == 0, result.output
    line = _preview(result.stderr, "PUT /rest/api/2/issue/ABC-1")[1]
    assert "(unchanged)" not in line
    assert " → " in line


@pytest.mark.parametrize(
    ("assignment", "expected"),
    [
        ('priority={"id":"2"}', "priority: 2 (unchanged)"),
        ('priority={"id":"3"}', "priority: 2 → 3"),
        ('priority={"name":"Low"}', "priority: High → Low"),
        ('components=[{"id":"10"}]', "components: [10] (unchanged)"),
        ('components=[{"name":"UI"}]', "components: [API] → [UI]"),
    ],
)
def test_patch_preview_names_old_values_the_way_the_new_value_does(
    assignment: str, expected: str
) -> None:
    result, _, _ = _run(["issues", "patch", "ABC-1", "--set-json", assignment, "--dry-run"])

    assert result.exit_code == 0, result.output
    assert _preview(result.stderr, "PUT /rest/api/2/issue/ABC-1")[1] == f"  {expected}"


def test_patch_preview_reads_only_fields_it_diffs(tmp_path: Path) -> None:
    fields_file = tmp_path / "edit.yml"
    fields_file.write_text(
        "update:\n  comment:\n    - add:\n        body: hi\n  labels:\n    - add: x\n"
        "  components:\n    - set: [{name: UI}]\n"
    )
    result, issue, _ = _run(
        ["issues", "patch", "ABC-1", "--fields-file", str(fields_file), "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert issue.calls[0].request.url.params["fields"] == "components"


def test_add_only_patch_preview_reads_nothing(tmp_path: Path) -> None:
    fields_file = tmp_path / "edit.yml"
    fields_file.write_text("update:\n  labels:\n    - add: x\n")
    result, issue, _ = _run(
        ["issues", "patch", "ABC-1", "--fields-file", str(fields_file), "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert len(issue.calls) == 0
    assert '  labels: + "x"' in result.stderr


def _transition_mock(
    mock: respx.MockRouter, key: str, *, status: int = 200
) -> tuple[respx.Route, respx.Route]:
    """Mock one issue's preview read and its transitions; return (issue, transitions)."""
    body = {**CURRENT, "key": key, "transitions": TRANSITIONS["transitions"]}
    issue = mock.get(f"/rest/api/2/issue/{key}").mock(
        return_value=httpx.Response(status, json=body if status == 200 else {})
    )
    listing = mock.get(f"/rest/api/2/issue/{key}/transitions").mock(
        return_value=httpx.Response(200, json=TRANSITIONS)
    )
    return issue, listing


def test_transition_preview_reuses_the_resolved_transition_for_each_key() -> None:
    with respx.mock(base_url=BASE) as mock:
        routes = [_transition_mock(mock, key) for key in ("ABC-1", "ABC-2")]
        result = invoke_cli(
            app, ["issues", "transition", "ABC-1", "ABC-2", "--to", "start progress", "--dry-run"]
        )

    assert result.exit_code == 0, result.output
    for key in ("ABC-1", "ABC-2"):
        assert _preview(result.stderr, f"POST /rest/api/2/issue/{key}/transitions")[1:3] == [
            "  transition: Start Progress (31)",
            "  status: To Do → In Progress",
        ]
    for issue, listing in routes:
        assert len(listing.calls) == 1  # resolving by name; the preview reuses it
        assert len(issue.calls) == 1
        assert "expand" not in issue.calls[0].request.url.params


def test_transition_preview_by_id_reads_the_issue_once() -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        issue, listing = _transition_mock(mock, "ABC-1")
        result = invoke_cli(app, ["issues", "transition", "ABC-1", "--id", "31", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert len(issue.calls) == 1
    assert issue.calls[0].request.url.params["expand"] == "transitions"
    assert len(listing.calls) == 0


def test_a_failed_preview_read_shows_unknown_instead_of_aborting() -> None:
    with respx.mock(base_url=BASE) as mock:
        _transition_mock(mock, "ABC-1")
        _transition_mock(mock, "ABC-2", status=500)
        result = invoke_cli(
            app, ["issues", "transition", "ABC-1", "ABC-2", "--to", "Start Progress", "--dry-run"]
        )

    assert result.exit_code == 0, result.output
    assert _preview(result.stderr, "POST /rest/api/2/issue/ABC-1/transitions")[2] == (
        "  status: To Do → In Progress"
    )
    assert _preview(result.stderr, "POST /rest/api/2/issue/ABC-2/transitions")[2] == (
        "  status: (unknown) → In Progress"
    )


def test_transition_preview_flags_an_unavailable_id() -> None:
    result, _, _ = _run(["issues", "transition", "ABC-1", "--id", "99", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert _preview(result.stderr, "POST /rest/api/2/issue/ABC-1/transitions")[1:] == [
        "  transition: 99",
        "  status: To Do → (not available from this status)",
    ]


def test_transition_preview_shows_the_whole_comment() -> None:
    comment = "c" * 100
    result, _, _ = _run(
        ["issues", "transition", "ABC-1", "--id", "31", "--comment", comment, "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert f"  comment:\n    {comment}\n" in result.stderr


def test_create_dry_run_stays_offline() -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route()
        result = invoke_cli(app, [*WRITES["create"], "--dry-run"])

    assert result.exit_code == 0, result.output
    assert len(route.calls) == 0


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
    root = bootstrap.build_root_app()
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        result = invoke_cli(root, ["jira", *args])

    assert result.exit_code == 2, result.output
    assert "deprecated" not in result.stderr
    assert len(route.calls) == 0
