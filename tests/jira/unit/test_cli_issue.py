"""End-to-end CLI tests for `untaped jira issue`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.jira.cli import app
from untaped.capabilities.jira.settings import JiraSettings
from untaped.settings import get_settings, register_profile_settings
from untaped.testing import CliInvoker


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    # Invoking the jira app directly skips the SDK profile-settings
    # registration, so mirror it (idempotent for the same model class)
    # before each test.
    register_profile_settings("jira", JiraSettings)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def jira_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "profiles:\n"
        "  default:\n"
        "    jira:\n"
        "      base_url: https://jira.example.com\n"
        "      token: jira_pat\n"
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


def test_me_reads_authenticated_jira_user(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis", "displayName": "Alexis"})
        )
        result = CliInvoker().invoke(app, ["me", "--format", "raw", "--columns", "name"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "alexis"


def test_me_table_renders_detail_view(jira_config: Path) -> None:
    """A single entity renders as a vertical key:value detail view under the
    default config — not a boxed one-row table (the ``emit`` single-record
    contract). Before the migration this default-config render was a grid."""
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis", "displayName": "Alexis"})
        )
        result = CliInvoker().invoke(app, ["me"])

    assert result.exit_code == 0, result.output
    assert "name: alexis" in result.stdout
    assert "displayName: Alexis" in result.stdout
    assert "╭" not in result.stdout


def test_me_raw_ignores_unknown_global_ui_theme(jira_config: Path) -> None:
    jira_config.write_text(
        "profiles:\n"
        "  default:\n"
        "    ui:\n"
        "      theme: missing\n"
        "    jira:\n"
        "      base_url: https://jira.example.com\n"
        "      token: jira_pat\n"
    )
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis", "displayName": "Alexis"})
        )
        result = CliInvoker().invoke(app, ["me", "--format", "raw", "--columns", "name"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "alexis"


def test_issue_get_renders_key_first(jira_config: Path) -> None:
    payload = {
        "key": "ABC-1",
        "self": "https://jira.example.com/rest/api/2/issue/10001",
        "fields": {
            "summary": "Fix deploy",
            "status": {"name": "In Progress"},
            "assignee": {"displayName": "Alexis"},
            "updated": "2026-06-05T10:00:00.000-0400",
        },
    }
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(200, json=payload))
        result = CliInvoker().invoke(
            app, ["issue", "get", "ABC-1", "--format", "raw", "--columns", "key"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC-1"


def test_issue_commands_missing_key_is_usage_error() -> None:
    runner = CliInvoker()

    for args in (
        ["issue", "get"],
        ["issue", "edit"],
        ["issue", "comment"],
        ["issue", "transitions"],
        ["issue", "transition"],
    ):
        result = runner.invoke(app, args)

        assert result.exit_code == 2, result.output
        assert result.stdout == ""
        assert "requires an argument" in result.stderr


def test_issue_search_sends_jql_and_renders_issue_keys(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "maxResults": 50,
                    "total": 1,
                    "issues": [{"key": "ABC-1", "fields": {"summary": "Fix deploy"}}],
                },
            )
        )
        result = CliInvoker().invoke(
            app,
            [
                "issue",
                "search",
                "--project",
                "ABC",
                "--status",
                "Open",
                "--format",
                "raw",
                "--columns",
                "key",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC-1"
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["jql"] == ('project = ABC AND status = "Open" ORDER BY updated DESC')


def test_issue_assigned_uses_default_assigned_jql(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "maxResults": 50,
                    "total": 1,
                    "issues": [{"key": "ABC-1", "fields": {"summary": "Fix deploy"}}],
                },
            )
        )
        result = CliInvoker().invoke(
            app,
            ["issue", "assigned", "--format", "raw", "--columns", "key"],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC-1"
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["jql"] == (
        "(assignee = currentUser() AND resolution = Unresolved) ORDER BY updated DESC"
    )


def test_issue_assigned_uses_configured_assigned_jql(jira_config: Path) -> None:
    jira_config.write_text(
        "profiles:\n"
        "  default:\n"
        "    jira:\n"
        "      base_url: https://jira.example.com\n"
        "      token: jira_pat\n"
        "      assigned_jql: assignee = currentUser() AND project = OPS\n"
    )
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "maxResults": 50,
                    "total": 1,
                    "issues": [{"key": "OPS-7", "fields": {"summary": "Patch release"}}],
                },
            )
        )
        result = CliInvoker().invoke(
            app,
            ["issue", "assigned", "--format", "raw", "--columns", "key"],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "OPS-7"
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["jql"] == (
        "(assignee = currentUser() AND project = OPS) ORDER BY updated DESC"
    )


def test_issue_assigned_jql_option_overrides_configured_assigned_jql(
    jira_config: Path,
) -> None:
    jira_config.write_text(
        "profiles:\n"
        "  default:\n"
        "    jira:\n"
        "      base_url: https://jira.example.com\n"
        "      token: jira_pat\n"
        "      assigned_jql: assignee = currentUser() AND project = OPS\n"
    )
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "maxResults": 50,
                    "total": 1,
                    "issues": [{"key": "SEC-3", "fields": {"summary": "Review ACL"}}],
                },
            )
        )
        result = CliInvoker().invoke(
            app,
            [
                "issue",
                "assigned",
                "--jql",
                "assignee = currentUser() AND project = SEC",
                "--status",
                "In Progress",
                "--format",
                "raw",
                "--columns",
                "key",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "SEC-3"
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["jql"] == (
        '(assignee = currentUser() AND project = SEC) AND status = "In Progress" '
        "ORDER BY updated DESC"
    )


def test_issue_assigned_rejects_blank_jql_override(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com", assert_all_called=False) as mock:
        route = mock.post("/rest/api/2/search").mock(
            return_value=httpx.Response(
                200,
                json={"startAt": 0, "maxResults": 50, "total": 0, "issues": []},
            )
        )
        result = CliInvoker().invoke(app, ["issue", "assigned", "--jql", ""])

    assert result.exit_code != 0
    assert "--jql must not be blank" in result.output
    assert len(route.calls) == 0


def test_issue_assigned_rejects_whitespace_jql_override(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com", assert_all_called=False) as mock:
        route = mock.post("/rest/api/2/search").mock(
            return_value=httpx.Response(
                200,
                json={"startAt": 0, "maxResults": 50, "total": 0, "issues": []},
            )
        )
        result = CliInvoker().invoke(app, ["issue", "assigned", "--jql", "   "])

    assert result.exit_code != 0
    assert "--jql must not be blank" in result.output
    assert len(route.calls) == 0


def test_issue_create_merges_template_and_flags(jira_config: Path, tmp_path: Path) -> None:
    template = tmp_path / "bug.yml"
    template.write_text("fields:\n  customfield_10000: old\n")
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/issue").mock(
            return_value=httpx.Response(
                201,
                json={"id": "10001", "key": "ABC-1", "self": "https://jira.example.com/ABC-1"},
            )
        )
        result = CliInvoker().invoke(
            app,
            [
                "issue",
                "create",
                "--template",
                str(template),
                "--project",
                "ABC",
                "--issue-type",
                "Bug",
                "--summary",
                "Fix deploy",
                "--field",
                "customfield_10000=new",
                "--json-field",
                'customfield_10001={"value":"prod"}',
                "--format",
                "raw",
                "--columns",
                "key",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC-1"
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["fields"] == {
        "customfield_10000": "new",
        "customfield_10001": {"value": "prod"},
        "project": {"key": "ABC"},
        "issuetype": {"name": "Bug"},
        "summary": "Fix deploy",
    }


def test_issue_create_invalid_json_field_is_usage_error(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com", assert_all_called=False) as mock:
        route = mock.post("/rest/api/2/issue").mock(
            return_value=httpx.Response(201, json={"id": "10001", "key": "ABC-1"})
        )
        result = CliInvoker().invoke(
            app,
            [
                "issue",
                "create",
                "--project",
                "ABC",
                "--summary",
                "Fix deploy",
                "--json-field",
                "customfield_10000={broken",
            ],
        )

    assert result.exit_code == 2, result.output
    assert "--json-field customfield_10000 contains invalid JSON" in result.stderr
    assert len(route.calls) == 0


def test_issue_edit_sends_body_file_and_overlays_flags(jira_config: Path, tmp_path: Path) -> None:
    body_file = tmp_path / "edit.yml"
    body_file.write_text("fields:\n  summary: old\n")
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.put("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(204))
        result = CliInvoker().invoke(
            app,
            [
                "issue",
                "edit",
                "ABC-1",
                "--body-file",
                str(body_file),
                "--summary",
                "new",
                "--field",
                "customfield_10000=value",
                "--format",
                "raw",
                "--columns",
                "key",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC-1"
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["fields"] == {
        "summary": "new",
        "customfield_10000": "value",
    }


def test_issue_comment_reads_body_from_stdin(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(201, json={"id": "700"})
        )
        result = CliInvoker().invoke(
            app,
            ["issue", "comment", "ABC-1", "--format", "raw", "--columns", "id"],
            input="hello from stdin\n",
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "700"
    assert json.loads(route.calls[0].request.content) == {"body": "hello from stdin"}


def test_issue_comment_missing_body_uses_sdk_error(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com", assert_all_called=False) as mock:
        route = mock.post("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(201, json={"id": "700"})
        )
        result = CliInvoker().invoke(app, ["issue", "comment", "ABC-1"])

    assert result.exit_code != 0
    assert "no body provided (use --body, --body-file, or pipe it on stdin)" in result.output
    assert len(route.calls) == 0


def test_issue_comment_table_render_fails_when_theme_is_unknown(jira_config: Path) -> None:
    jira_config.write_text(
        "profiles:\n"
        "  default:\n"
        "    ui:\n"
        "      theme: missing\n"
        "    jira:\n"
        "      base_url: https://jira.example.com\n"
        "      token: jira_pat\n"
    )
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(201, json={"id": "700"})
        )
        result = CliInvoker().invoke(app, ["issue", "comment", "ABC-1", "--body", "hello"])

    assert result.exit_code != 0
    assert "unknown UI theme" in result.output
    assert len(route.calls) == 1


def test_issue_comment_preserves_formatted_stdin_body(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(201, json={"id": "700"})
        )
        result = CliInvoker().invoke(
            app,
            ["issue", "comment", "ABC-1", "--format", "raw", "--columns", "id"],
            input="line1\n\n    code\nline3\n",
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "700"
    assert json.loads(route.calls[0].request.content) == {"body": "line1\n\n    code\nline3"}


def test_issue_comment_preserves_formatted_body_file(
    jira_config: Path,
    tmp_path: Path,
) -> None:
    body_file = tmp_path / "comment.md"
    body_file.write_text("line1\n\n    code\nline3\n")
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(201, json={"id": "700"})
        )
        result = CliInvoker().invoke(
            app,
            [
                "issue",
                "comment",
                "ABC-1",
                "--body-file",
                str(body_file),
                "--format",
                "raw",
                "--columns",
                "id",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "700"
    assert json.loads(route.calls[0].request.content) == {"body": "line1\n\n    code\nline3"}


def test_issue_transition_by_name_rejects_ambiguous_match(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(
                200,
                json={"transitions": [{"id": "1", "name": "Done"}, {"id": "2", "name": "done"}]},
            )
        )
        result = CliInvoker().invoke(app, ["issue", "transition", "ABC-1", "--to", "done"])

    assert result.exit_code != 0
    assert "multiple transitions" in result.output or "multiple transitions" in str(
        result.exception
    )


def test_issue_transition_by_id_posts_transition(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(204)
        )
        result = CliInvoker().invoke(
            app,
            [
                "issue",
                "transition",
                "ABC-1",
                "--id",
                "31",
                "--format",
                "raw",
                "--columns",
                "transition_id",
            ],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "31"
    assert json.loads(route.calls[0].request.content) == {"transition": {"id": "31"}}


def _empty_search() -> httpx.Response:
    return httpx.Response(200, json={"startAt": 0, "maxResults": 50, "total": 0, "issues": []})


def test_issue_search_empty_guides_with_stderr_hint(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/search").mock(return_value=_empty_search())
        result = CliInvoker().invoke(app, ["issue", "search", "--project", "ABC"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No issues match the query" in result.stderr


def test_issue_search_empty_json_stays_pipe_clean(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/search").mock(return_value=_empty_search())
        result = CliInvoker().invoke(
            app, ["issue", "search", "--project", "ABC", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "[]"
    assert "No issues match the query" not in result.stderr


def test_issue_search_reports_progress_on_stderr(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "maxResults": 50,
                    "total": 1,
                    "issues": [{"key": "ABC-1", "fields": {"summary": "Fix deploy"}}],
                },
            )
        )
        result = CliInvoker().invoke(
            app, ["issue", "search", "--project", "ABC", "--format", "raw", "--columns", "key"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC-1"
    assert "Querying Jira issues" in result.stderr
    assert "Querying Jira issues" not in result.stdout


def test_issue_assigned_empty_guides_with_stderr_hint(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/search").mock(return_value=_empty_search())
        result = CliInvoker().invoke(app, ["issue", "assigned"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No issues assigned to you" in result.stderr


def test_issue_transitions_empty_guides_with_stderr_hint(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(200, json={"transitions": []})
        )
        result = CliInvoker().invoke(app, ["issue", "transitions", "ABC-1"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No transitions available for this issue" in result.stderr
