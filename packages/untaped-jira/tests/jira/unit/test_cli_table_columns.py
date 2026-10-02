"""CLI tests for the jira commands' default ``table`` columns.

``--columns ?`` marks a command's default table columns with ``*``; every
other format keeps the whole record.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from untaped.testing import invoke_cli
from untaped_jira.cli import app

BASE = "https://jira.example.com"

_ISSUE = {
    "key": "ABC-1",
    "self": f"{BASE}/rest/api/2/issue/10001",
    "fields": {
        "summary": "Fix deploy",
        "status": {"name": "Open"},
        "issuetype": {"name": "Bug"},
        "priority": {"name": "High"},
        "assignee": {"displayName": "Alexis"},
        "updated": "2026-06-05T10:00:00.000-0400",
    },
}
_SEARCH = {"startAt": 0, "maxResults": 50, "total": 1, "issues": [_ISSUE]}
_COMMENTS = {
    "startAt": 0,
    "maxResults": 50,
    "total": 1,
    "comments": [{"id": "100", "author": {"displayName": "Sam"}, "body": "hi"}],
}
_TRANSITIONS = {"transitions": [{"id": "11", "name": "Start", "to": {"name": "In Progress"}}]}
_AGILE_PAGE = {"startAt": 0, "maxResults": 50, "isLast": True}


def _defaults(argv: list[str]) -> list[str]:
    """The default table columns ``argv`` marks under ``--columns ?`` (in record order)."""
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post("/rest/api/2/search").mock(return_value=httpx.Response(200, json=_SEARCH))
        for key in ("ABC-1", "ABC-2"):
            mock.get(f"/rest/api/2/issue/{key}").mock(
                return_value=httpx.Response(200, json={**_ISSUE, "key": key})
            )
            mock.post(f"/rest/api/2/issue/{key}/transitions").mock(return_value=httpx.Response(204))
        mock.get("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(200, json=_COMMENTS)
        )
        mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(200, json=_TRANSITIONS)
        )
        mock.get("/rest/api/2/project").mock(
            return_value=httpx.Response(
                200, json=[{"key": "ABC", "name": "Alpha", "id": "1", "projectTypeKey": "software"}]
            )
        )
        mock.get("/rest/agile/1.0/board").mock(
            return_value=httpx.Response(
                200, json={**_AGILE_PAGE, "values": [{"id": 7, "name": "B", "type": "scrum"}]}
            )
        )
        mock.get("/rest/agile/1.0/board/7/sprint").mock(
            return_value=httpx.Response(
                200,
                json={**_AGILE_PAGE, "values": [{"id": 3, "name": "S", "originBoardId": 7}]},
            )
        )
        result = invoke_cli(app, [*argv, "--columns", "?"])
    assert result.exit_code == 0, result.output
    return [line.split()[0] for line in result.stderr.splitlines() if line.endswith(" *")]


_ISSUE_COLUMNS = ["key", "issue_type", "status", "priority", "assignee", "summary", "updated_at"]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["issues", "search", "--project", "ABC"], _ISSUE_COLUMNS),
        (
            ["issues", "assigned"],
            ["key", "issue_type", "status", "priority", "summary", "updated_at"],
        ),
        (["issues", "get", "ABC-1", "ABC-2"], _ISSUE_COLUMNS),
        (["issues", "comments", "list", "ABC-1"], ["author", "created_at", "body"]),
        (["issues", "transitions", "ABC-1"], ["id", "name", "to_status"]),
        (
            ["issues", "transition", "ABC-1", "ABC-2", "--id", "11", "--yes"],
            ["key", "transition_id", "action"],
        ),
        (["projects", "list"], ["key", "name", "project_type_key"]),
        (["boards", "list"], ["id", "name", "type"]),
        (
            ["sprints", "list", "--board-id", "7"],
            ["id", "name", "state", "start_at", "end_at", "goal"],
        ),
    ],
)
def test_default_table_columns(argv: list[str], expected: list[str]) -> None:
    assert set(_defaults(argv)) == set(expected)


def test_search_rows_carry_issue_type_and_priority() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/search").mock(return_value=httpx.Response(200, json=_SEARCH))
        result = invoke_cli(app, ["issues", "search", "--project", "ABC", "--format", "json"])

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert (row["issue_type"], row["priority"]) == ("Bug", "High")
    assert {"issuetype", "priority"} <= set(json.loads(route.calls[0].request.content)["fields"])


def test_search_table_shows_issue_type_and_priority_not_urls() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.post("/rest/api/2/search").mock(return_value=httpx.Response(200, json=_SEARCH))
        result = invoke_cli(app, ["issues", "search", "--project", "ABC"])

    assert result.exit_code == 0, result.output
    header = result.stdout.splitlines()[1]
    for column in ("issue_type", "priority", "assignee"):
        assert column in header
    assert "api_url" not in header
    assert BASE not in result.stdout


def test_transitions_carry_their_target_status() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(200, json=_TRANSITIONS)
        )
        result = invoke_cli(app, ["issues", "transitions", "ABC-1", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"id": "11", "name": "Start", "to_status": "In Progress"}]


def test_get_comments_table_keeps_issue_key_only_for_several_issues() -> None:
    with respx.mock(base_url=BASE) as mock:
        for key in ("ABC-1", "ABC-2"):
            mock.get(f"/rest/api/2/issue/{key}").mock(
                return_value=httpx.Response(200, json={**_ISSUE, "key": key})
            )
            mock.get(f"/rest/api/2/issue/{key}/comment").mock(
                return_value=httpx.Response(200, json=_COMMENTS)
            )
        several = invoke_cli(app, ["issues", "get", "ABC-1", "ABC-2", "--comments"])
        one = invoke_cli(app, ["issues", "get", "ABC-1", "--comments"])

    assert several.exit_code == 0, several.output
    assert one.exit_code == 0, one.output
    assert "issue_key" in several.stdout
    assert "issue_key" not in one.stdout
    assert "author" in one.stdout


def test_a_transition_without_a_target_has_no_status() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(200, json={"transitions": [{"id": "11", "name": "Start"}]})
        )
        result = invoke_cli(app, ["issues", "transitions", "ABC-1", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"id": "11", "name": "Start", "to_status": None}]
