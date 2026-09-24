"""The output contract of every jira producer command.

Each command tags its ``--format pipe`` records with a namespaced ``kind``,
a single entity renders as a bare ``--format json`` object (a list command
as an array), and an empty list hints on stderr in a table but stays
pipe-clean as ``[]`` in json.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from untaped.capabilities.jira.cli import app
from untaped.testing import CliInvoker

BASE = "https://jira.example.com"
_ISSUE = {"key": "ABC-1", "fields": {"summary": "Fix deploy"}}
_CREATED = {"id": "10001", "key": "ABC-1", "self": f"{BASE}/rest/api/2/issue/10001"}


def _search(*issues: object) -> dict[str, object]:
    return {"startAt": 0, "maxResults": 50, "total": len(issues), "issues": list(issues)}


def _agile(*values: object) -> dict[str, object]:
    return {"startAt": 0, "maxResults": 50, "isLast": True, "values": list(values)}


# (argv, method, path, status, body, kind, single entity)
PRODUCERS: list[tuple[list[str], str, str, int, Any, str, bool]] = [
    (["whoami"], "GET", "/rest/api/2/myself", 200, {"name": "alexis"}, "jira.user", True),
    (["issues", "get", "ABC-1"], "GET", "/rest/api/2/issue/ABC-1", 200, _ISSUE, "jira.issue", True),
    (
        ["issues", "search", "--project", "ABC"],
        "POST",
        "/rest/api/2/search",
        200,
        _search(_ISSUE),
        "jira.issue",
        False,
    ),
    (
        ["issues", "transitions", "ABC-1"],
        "GET",
        "/rest/api/2/issue/ABC-1/transitions",
        200,
        {"transitions": [{"id": "1", "name": "Done"}]},
        "jira.transition",
        False,
    ),
    (
        ["projects", "list"],
        "GET",
        "/rest/api/2/project",
        200,
        [{"id": "10000", "key": "ABC", "name": "App"}],
        "jira.project",
        False,
    ),
    (
        ["projects", "get", "ABC"],
        "GET",
        "/rest/api/2/project/ABC",
        200,
        {"id": "10000", "key": "ABC", "name": "App"},
        "jira.project",
        True,
    ),
    (
        ["boards", "list"],
        "GET",
        "/rest/agile/1.0/board",
        200,
        _agile({"id": 7, "name": "ABC Board", "type": "scrum"}),
        "jira.board",
        False,
    ),
    (
        ["sprints", "list", "--board-id", "7"],
        "GET",
        "/rest/agile/1.0/board/7/sprint",
        200,
        _agile({"id": 20, "name": "Sprint 20", "state": "active"}),
        "jira.sprint",
        False,
    ),
    # A mutation result is a ``jira.issue_outcome``, not the ``jira.issue`` entity.
    (
        ["issues", "create", "--yes", "--project", "ABC", "--summary", "x"],
        "POST",
        "/rest/api/2/issue",
        201,
        _CREATED,
        "jira.issue_outcome",
        True,
    ),
    (
        ["issues", "comment", "ABC-1", "--yes", "--body", "hi"],
        "POST",
        "/rest/api/2/issue/ABC-1/comment",
        201,
        {"id": "700"},
        "jira.issue_outcome",
        True,
    ),
    (
        ["issues", "transition", "ABC-1", "--yes", "--id", "31"],
        "POST",
        "/rest/api/2/issue/ABC-1/transitions",
        204,
        None,
        "jira.issue_outcome",
        True,
    ),
]


@pytest.mark.parametrize(
    ("argv", "method", "path", "status", "body", "kind", "single"),
    PRODUCERS,
    ids=[" ".join(case[0][:2]) for case in PRODUCERS],
)
def test_producers_tag_pipe_records_and_emit_single_entities_bare(
    argv: list[str], method: str, path: str, status: int, body: Any, kind: str, single: bool
) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.route(method=method, path=path).mock(return_value=httpx.Response(status, json=body))
        piped = CliInvoker().invoke(app, [*argv, "--format", "pipe"])
        as_json = CliInvoker().invoke(app, [*argv, "--format", "json"])

    assert piped.exit_code == 0, piped.output
    envelopes = [json.loads(line) for line in piped.stdout.splitlines()]
    assert len(envelopes) == 1
    assert envelopes[0]["untaped"] == "1"
    assert envelopes[0]["kind"] == kind
    assert as_json.exit_code == 0, as_json.output
    assert isinstance(json.loads(as_json.stdout), dict if single else list)


def test_issue_outcome_record_shape() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.post("/rest/api/2/issue").mock(return_value=httpx.Response(201, json=_CREATED))
        result = CliInvoker().invoke(
            app,
            ["issues", "create", "--yes", "--project", "ABC", "--summary", "x", "--format", "pipe"],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["record"] == {
        "action": "created",
        "key": "ABC-1",
        "id": "10001",
        "url": f"{BASE}/browse/ABC-1",
        "api_url": f"{BASE}/rest/api/2/issue/10001",
        "transition_id": None,
        "comment_id": None,
        "link_type": None,
        "linked_key": None,
    }


@pytest.mark.parametrize(
    ("argv", "path", "body", "hint"),
    [
        (
            ["issues", "search", "--project", "ABC"],
            "/rest/api/2/search",
            _search(),
            "No issues match the query.",
        ),
        (["issues", "assigned"], "/rest/api/2/search", _search(), "No issues assigned to you."),
        (
            ["issues", "transitions", "ABC-1"],
            "/rest/api/2/issue/ABC-1/transitions",
            {"transitions": []},
            "No transitions available for this issue.",
        ),
        (
            ["issues", "comments", "list", "ABC-1"],
            "/rest/api/2/issue/ABC-1/comment",
            {"startAt": 0, "maxResults": 50, "total": 0, "comments": []},
            "No comments found.",
        ),
        (["projects", "list"], "/rest/api/2/project", [], "No projects are visible to you."),
        (["boards", "list"], "/rest/agile/1.0/board", _agile(), "No boards match the filter."),
        (
            ["sprints", "list", "--board-id", "7"],
            "/rest/agile/1.0/board/7/sprint",
            _agile(),
            "No sprints found for this board.",
        ),
    ],
)
def test_empty_lists_hint_in_a_table_and_stay_clean_in_json(
    argv: list[str], path: str, body: Any, hint: str
) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.route(path=path).mock(return_value=httpx.Response(200, json=body))
        table = CliInvoker().invoke(app, argv)
        as_json = CliInvoker().invoke(app, [*argv, "--format", "json"])

    assert table.exit_code == 0, table.output
    assert table.stdout == ""
    assert hint in table.stderr
    assert as_json.exit_code == 0, as_json.output
    assert as_json.stdout.strip() == "[]"
    assert hint not in as_json.stderr
