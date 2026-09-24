"""CLI tests for the richer Jira issue workflow.

Covers ``issues get --comments`` and its table columns, ``issues comments
list``, assigning through ``issues patch``, ``transition --comment/--resolution``
and ``issues links create``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from untaped.capabilities.jira.cli import app
from untaped.testing import invoke_cli

BASE = "https://jira.example.com"


def _issue(key: str) -> dict[str, object]:
    return {
        "key": key,
        "self": f"{BASE}/rest/api/2/issue/{key}",
        "fields": {
            "summary": f"Summary of {key}",
            "status": {"name": "Open"},
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "description": "A long description.",
        },
    }


def _comments(*bodies: str) -> dict[str, object]:
    return {
        "startAt": 0,
        "maxResults": 50,
        "total": len(bodies),
        "comments": [
            {
                "id": str(100 + index),
                "self": f"{BASE}/rest/api/2/issue/10001/comment/{100 + index}",
                "author": {"name": "sam", "displayName": "Sam"},
                "body": body,
                "created": "2026-06-01T09:00:00.000-0400",
                "updated": "2026-06-01T09:00:00.000-0400",
            }
            for index, body in enumerate(bodies)
        ],
    }


# --- issues get ----------------------------------------------------------------


def test_get_with_comments_nests_them_in_structured_output() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-1").mock(
            return_value=httpx.Response(200, json=_issue("ABC-1"))
        )
        mock.get("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(200, json=_comments("first", "second"))
        )
        result = invoke_cli(app, ["issues", "get", "ABC-1", "--comments", "--format", "json"])

    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert [(c["id"], c["issue_key"], c["author"], c["body"]) for c in row["comments"]] == [
        ("100", "ABC-1", "Sam", "first"),
        ("101", "ABC-1", "Sam", "second"),
    ]
    assert row["comments"][0]["created_at"] == "2026-06-01T13:00:00Z"


def test_get_without_comments_fetches_none() -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/rest/api/2/issue/ABC-1").mock(
            return_value=httpx.Response(200, json=_issue("ABC-1"))
        )
        comments = mock.get("/rest/api/2/issue/ABC-1/comment")
        json_result = invoke_cli(app, ["issues", "get", "ABC-1", "--format", "json"])
        table_result = invoke_cli(app, ["issues", "get", "ABC-1"])

    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.stdout)["comments"] is None
    assert len(comments.calls) == 0
    assert "description: A long description." in table_result.stdout
    assert "comments" not in table_result.stdout


def test_get_table_lists_comments_after_the_detail() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-1").mock(
            return_value=httpx.Response(200, json=_issue("ABC-1"))
        )
        mock.get("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(200, json=_comments("looks good"))
        )
        result = invoke_cli(app, ["issues", "get", "ABC-1", "--comments"])

    assert result.exit_code == 0, result.output
    detail, _, comments = result.stdout.partition("╭")
    assert "priority: High" in detail
    assert "looks good" in comments
    assert "Sam" in comments
    assert "{'" not in result.stdout


def test_get_several_issues_table_uses_compact_columns() -> None:
    with respx.mock(base_url=BASE) as mock:
        for key in ("ABC-1", "ABC-2"):
            mock.get(f"/rest/api/2/issue/{key}").mock(
                return_value=httpx.Response(200, json=_issue(key))
            )
        result = invoke_cli(app, ["issues", "get", "ABC-1", "ABC-2"])

    assert result.exit_code == 0, result.output
    header = result.stdout.splitlines()[1]
    for column in ("key", "issue_type", "status", "priority", "summary"):
        assert column in header
    assert "description" not in result.stdout


# --- issues comments list --------------------------------------------------------


def test_comments_list_emits_comment_records() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(200, json=_comments("one", "two"))
        )
        result = invoke_cli(app, ["issues", "comments", "list", "ABC-1", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert {record["kind"] for record in records} == {"jira.comment"}
    assert [record["record"]["body"] for record in records] == ["one", "two"]
    assert route.calls[0].request.url.params["startAt"] == "0"


# --- assign via issues patch -------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--assignee", "bob"], {"name": "bob"}),
        (["--unassign"], None),
    ],
)
def test_patch_assignee_sets_the_assignee_field(args: list[str], expected: object) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.put("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(204))
        result = invoke_cli(app, ["issues", "patch", "ABC-1", *args, "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content) == {"fields": {"assignee": expected}}
    assert json.loads(result.stdout)["action"] == "updated"


def test_patch_assignee_me_resolves_the_authenticated_user() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis", "displayName": "Alexis"})
        )
        route = mock.put("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(204))
        result = invoke_cli(app, ["issues", "patch", "ABC-1", "--assignee", "@me", "--yes"])

    assert result.exit_code == 0, result.output
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"fields": {"assignee": {"name": "alexis"}}}


def test_patch_assignee_me_dry_run_shows_the_resolved_name() -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis"})
        )
        route = mock.put("/rest/api/2/issue/ABC-1")
        result = invoke_cli(app, ["issues", "patch", "ABC-1", "--assignee", "@me", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert '"name": "alexis"' in result.stderr
    assert len(route.calls) == 0


# --- transition --comment / --resolution ---------------------------------------------


def test_transition_sends_comment_and_resolution() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(204)
        )
        result = invoke_cli(
            app,
            [
                "issues",
                "transition",
                "ABC-1",
                "--id",
                "31",
                "--comment",
                "Shipped in 1.2.",
                "--resolution",
                "Fixed",
                "--yes",
            ],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content) == {
        "transition": {"id": "31"},
        "fields": {"resolution": {"name": "Fixed"}},
        "update": {"comment": [{"add": {"body": "Shipped in 1.2."}}]},
    }


def test_transition_dry_run_previews_comment_and_resolution() -> None:
    result = invoke_cli(
        app,
        ["issues", "transition", "ABC-1", "--id", "31", "--resolution", "Done", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert '"resolution"' in result.stderr
    assert "POST /rest/api/2/issue/ABC-1/transitions" in result.stderr


# --- issues links create ---------------------------------------------------------------


def test_links_create_posts_an_issue_link() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/issueLink").mock(return_value=httpx.Response(201))
        result = invoke_cli(
            app,
            ["issues", "links", "create", "ABC-1", "Blocks", "ABC-2", "--yes", "--format", "json"],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content) == {
        "type": {"name": "Blocks"},
        "inwardIssue": {"key": "ABC-1"},
        "outwardIssue": {"key": "ABC-2"},
    }
    row = json.loads(result.stdout)
    assert (row["action"], row["key"], row["link_type"], row["linked_key"]) == (
        "linked",
        "ABC-1",
        "Blocks",
        "ABC-2",
    )


def test_links_create_dry_run_states_the_direction_in_words() -> None:
    result = invoke_cli(app, ["issues", "links", "create", "ABC-1", "Blocks", "ABC-2", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "reads as: ABC-1 <outward phrase of 'Blocks'> ABC-2" in result.stderr
    assert "POST /rest/api/2/issueLink" in result.stderr
