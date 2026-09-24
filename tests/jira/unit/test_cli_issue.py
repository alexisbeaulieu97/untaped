"""End-to-end CLI tests for `untaped jira issues`: rows, JQL, and request payloads."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.jira.cli import app
from untaped.testing import CliInvoker

BASE = "https://jira.example.com"
_THEMELESS_CONFIG = (
    "profiles:\n  default:\n    ui:\n      theme: missing\n"
    f"    jira:\n      base_url: {BASE}\n      token: jira_pat\n"
)


def _search(*keys: str) -> httpx.Response:
    issues = [{"key": key, "fields": {"summary": "x"}} for key in keys]
    return httpx.Response(
        200, json={"startAt": 0, "maxResults": 50, "total": len(issues), "issues": issues}
    )


def test_me_table_renders_detail_view() -> None:
    """A single entity renders as a vertical key:value detail view under the
    default config — not a boxed one-row table (the ``emit`` single-record
    contract). Before the migration this default-config render was a grid."""
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis", "displayName": "Alexis"})
        )
        result = CliInvoker().invoke(app, ["whoami"])

    assert result.exit_code == 0, result.output
    assert "name: alexis" in result.stdout
    assert "display_name: Alexis" in result.stdout
    assert "╭" not in result.stdout


def test_unknown_ui_theme_spares_raw_data_but_fails_a_table_render(jira_config: Path) -> None:
    jira_config.write_text(_THEMELESS_CONFIG)
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/myself").mock(return_value=httpx.Response(200, json={"name": "a"}))
        route = mock.post("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(201, json={"id": "700"})
        )
        raw = CliInvoker().invoke(app, ["whoami", "--format", "raw", "--columns", "name"])
        table = CliInvoker().invoke(app, ["issues", "comment", "ABC-1", "--yes", "--body", "hi"])

    assert raw.exit_code == 0, raw.output
    assert raw.stdout.strip() == "a"
    assert table.exit_code != 0
    assert "unknown UI theme" in table.output
    assert len(route.calls) == 1


def test_issue_get_shows_detail_fields() -> None:
    payload = {
        "key": "ABC-1",
        "self": f"{BASE}/rest/api/2/issue/10001",
        "fields": {
            "summary": "Fix deploy",
            "status": {"name": "In Progress"},
            "assignee": {"displayName": "Alexis"},
            "updated": "2026-06-05T10:00:00.000-0400",
            "description": "Deploy fails on step 3.",
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "reporter": {"displayName": "Sam"},
            "labels": ["deploy", "urgent"],
            "created": "2026-06-01T09:00:00.000-0400",
            "resolution": None,
        },
    }
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/rest/api/2/issue/ABC-1").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = CliInvoker().invoke(app, ["issues", "get", "ABC-1", "--format", "json"])

    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert row["key"] == "ABC-1"
    assert row["status"] == "In Progress"
    assert row["assignee"] == "Alexis"
    assert row["description"] == "Deploy fails on step 3."
    assert row["issue_type"] == "Bug"
    assert row["priority"] == "High"
    assert row["reporter"] == "Sam"
    assert row["labels"] == ["deploy", "urgent"]
    assert row["created_at"] == "2026-06-01T13:00:00Z"
    assert row["updated_at"] == "2026-06-05T14:00:00Z"
    assert row["url"] == f"{BASE}/browse/ABC-1"
    assert row["api_url"] == f"{BASE}/rest/api/2/issue/10001"
    assert row["resolution"] == ""
    requested = set(route.calls[0].request.url.params["fields"].split(","))
    assert {"description", "issuetype", "priority", "reporter", "labels", "created"} <= requested
    assert "resolution" in requested


def test_issue_search_rows_stay_lean() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/search").mock(return_value=_search("ABC-1"))
        result = CliInvoker().invoke(
            app, ["issues", "search", "--project", "ABC", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert set(row) == {"key", "summary", "status", "assignee", "updated_at", "url", "api_url"}
    body = json.loads(route.calls[0].request.content)
    assert body["fields"] == ["summary", "status", "assignee", "updated"]


_OPS_SCOPE = "assignee = currentUser() AND project = OPS"


@pytest.mark.parametrize(
    ("assigned_jql", "args", "jql"),
    [
        (
            None,
            ["search", "--project", "ABC", "--status", "Open"],
            'project = ABC AND status = "Open" ORDER BY updated DESC',
        ),
        # Without filters `search` falls back to jira.assigned_jql ...
        (_OPS_SCOPE, ["search"], f"{_OPS_SCOPE} ORDER BY updated DESC"),
        # ... while `assigned` always scopes by it (default or configured).
        (
            None,
            ["assigned"],
            "(assignee = currentUser() AND resolution = Unresolved) ORDER BY updated DESC",
        ),
        (_OPS_SCOPE, ["assigned"], f"({_OPS_SCOPE}) ORDER BY updated DESC"),
        (
            _OPS_SCOPE,
            [
                "assigned",
                "--jql",
                "project = SEC ORDER BY priority DESC",
                "--status",
                "In Progress",
            ],
            f'({_OPS_SCOPE}) AND (project = SEC) AND status = "In Progress" ORDER BY priority DESC',
        ),
    ],
)
def test_issue_search_and_assigned_send_rendered_jql(
    jira_config: Path, assigned_jql: str | None, args: list[str], jql: str
) -> None:
    if assigned_jql is not None:
        jira_config.write_text(jira_config.read_text() + f"      assigned_jql: {assigned_jql}\n")
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/search").mock(return_value=_search("ABC-1"))
        result = CliInvoker().invoke(app, ["issues", *args, "--format", "raw", "--columns", "key"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC-1"
    assert json.loads(route.calls[0].request.content)["jql"] == jql


def test_issue_create_merges_template_and_flags(tmp_path: Path) -> None:
    template = tmp_path / "bug.yml"
    template.write_text(
        "fields:\n  project:\n    key: OLD\n  summary: old\n  customfield_10000: old\n"
        "update:\n  labels:\n    - add: old\n"
    )
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/issue").mock(
            return_value=httpx.Response(201, json={"id": "10001", "key": "ABC-1"})
        )
        result = CliInvoker().invoke(
            app,
            [
                "issues",
                "create",
                "--yes",
                "--template",
                str(template),
                "--project",
                "ABC",
                "--issue-type",
                "Bug",
                "--summary",
                "Fix deploy",
                "--description",
                "new body",
                "--set",
                "customfield_10000=new",
                "--set-json",
                'customfield_10001={"value":"prod"}',
                "--format",
                "raw",
                "--columns",
                "key",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC-1"
    assert json.loads(route.calls[0].request.content) == {
        "fields": {
            "project": {"key": "ABC"},
            "summary": "Fix deploy",
            "customfield_10000": "new",
            "issuetype": {"name": "Bug"},
            "description": "new body",
            "customfield_10001": {"value": "prod"},
        },
        "update": {"labels": [{"add": "old"}]},
    }


@pytest.mark.parametrize(
    ("args", "body", "message"),
    [
        (
            ["create", "--project", "ABC", "--template"],
            "fields: []\n",
            "`fields` must be an object",
        ),
        (["patch", "ABC-1", "--summary", "x", "--body-file"], "update: []\n", "`update` must be"),
    ],
)
def test_non_object_payload_sections_are_rejected(
    tmp_path: Path, args: list[str], body: str, message: str
) -> None:
    payload = tmp_path / "payload.yml"
    payload.write_text(body)
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route()
        result = CliInvoker().invoke(app, ["issues", *args, str(payload), "--yes"])

    assert result.exit_code == 1, result.output
    assert message in result.stderr
    assert len(route.calls) == 0


def test_issue_patch_sends_body_file_and_overlays_flags(tmp_path: Path) -> None:
    body_file = tmp_path / "edit.yml"
    body_file.write_text("fields:\n  summary: old\n")
    with respx.mock(base_url=BASE) as mock:
        route = mock.put("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(204))
        result = CliInvoker().invoke(
            app,
            [
                "issues",
                "patch",
                "ABC-1",
                "--yes",
                "--body-file",
                str(body_file),
                "--summary",
                "new",
                "--set",
                "customfield_10000=value",
            ],
        )

    assert result.exit_code == 0, result.output
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["fields"] == {"summary": "new", "customfield_10000": "value"}


def test_issue_patch_without_changes_is_usage_error(tmp_path: Path) -> None:
    empty_body = tmp_path / "empty.yml"
    empty_body.write_text("fields: {}\n")
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.put("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(204))
        bare = CliInvoker().invoke(app, ["issues", "patch", "ABC-1"])
        empty = CliInvoker().invoke(
            app, ["issues", "patch", "ABC-1", "--body-file", str(empty_body)]
        )

    for result in (bare, empty):
        assert result.exit_code == 2, result.output
        assert "nothing to update" in result.stderr
    assert len(route.calls) == 0


def test_issue_patch_with_only_update_operations_is_sent(tmp_path: Path) -> None:
    body_file = tmp_path / "labels.yml"
    body_file.write_text("update:\n  labels:\n    - add: urgent\n")
    with respx.mock(base_url=BASE) as mock:
        route = mock.put("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(204))
        result = CliInvoker().invoke(
            app, ["issues", "patch", "ABC-1", "--yes", "--body-file", str(body_file)]
        )

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content)["update"] == {"labels": [{"add": "urgent"}]}


@pytest.mark.parametrize("source", ["stdin", "file"])
def test_issue_comment_body_keeps_its_formatting(tmp_path: Path, source: str) -> None:
    text = "line1\n\n    code\nline3\n"
    body_file = tmp_path / "comment.md"
    body_file.write_text(text)
    args = ["issues", "comment", "ABC-1", "--yes", "--format", "raw", "--columns", "comment_id"]
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(201, json={"id": "700"})
        )
        if source == "stdin":
            result = CliInvoker().invoke(app, args, input=text)
        else:
            result = CliInvoker().invoke(app, [*args, "--body-file", str(body_file)])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "700"
    assert json.loads(route.calls[0].request.content) == {"body": "line1\n\n    code\nline3"}


def test_issue_comment_missing_body_uses_sdk_error() -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route()
        result = CliInvoker().invoke(app, ["issues", "comment", "ABC-1", "--yes"])

    assert result.exit_code == 1, result.output
    assert "no body provided (use --body, --body-file, or pipe it on stdin)" in result.stderr
    assert len(route.calls) == 0


def test_issue_transition_by_name_rejects_ambiguous_match() -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(
                200,
                json={"transitions": [{"id": "1", "name": "Done"}, {"id": "2", "name": "done"}]},
            )
        )
        post = mock.post("/rest/api/2/issue/ABC-1/transitions")
        result = CliInvoker().invoke(
            app, ["issues", "transition", "ABC-1", "--yes", "--to", "done"]
        )

    assert result.exit_code == 1, result.output
    assert "multiple transitions named 'done' are available for ABC-1" in result.stderr
    assert len(post.calls) == 0
