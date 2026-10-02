"""CLI tests for the jira command conventions (``docs/plugins.md#conventions``).

Covers usage errors (exit 2), the write confirmation contract (``--yes`` /
``--dry-run``, under ``jira.confirm: always``), ``--stdin`` identifiers, and
HTTP error mapping.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from untaped.testing import CliInvoker, ScriptedPromptBackend, invoke_cli
from untaped_jira.cli import app

BASE = "https://jira.example.com"


def _issue(key: str) -> dict[str, object]:
    return {"key": key, "self": f"{BASE}/rest/api/2/issue/{key}", "fields": {"summary": key}}


# --- usage errors ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["issues", "transition", "ABC-1", "--yes"], "provide exactly one of --id or --to"),
        (
            ["issues", "transition", "ABC-1", "--yes", "--id", "1", "--to", "Done"],
            "provide exactly one of --id or --to",
        ),
        (["issues", "assigned", "--jql", ""], "--jql must not be blank"),
        (["issues", "assigned", "--jql", "  "], "--jql must not be blank"),
        (["sprints", "list"], "board id is required"),
        (["issues", "search", "--limit", "0"], "--limit"),
        (["issues", "patch"], "requires an argument"),
        (["issues", "comment"], "requires an argument"),
        (["issues", "transitions"], "requires an argument"),
        (["projects", "get"], "requires an argument"),
        (["issues", "get"], "at least one identifier is required"),
        (["issues", "transition", "--id", "31"], "at least one identifier is required"),
        (
            ["issues", "patch", "ABC-1", "--assignee", "bob", "--unassign", "--yes"],
            "pass either --assignee or --unassign, not both",
        ),
        (
            ["issues", "create", "--yes", "--project", "ABC", "--set-json", "cf={broken"],
            "--set-json cf contains invalid JSON",
        ),
        (["issues", "get", "ABC-1", "../../x"], "invalid issue key '../../x'"),
        (["issues", "patch", "A-1?x=y", "--summary", "x", "--yes"], "invalid issue key"),
        (["issues", "comment", "ABC-1/..", "--body", "hi", "--yes"], "invalid issue key"),
        (["issues", "comments", "list", "ABC-1#x"], "invalid issue key"),
        (["issues", "transitions", "1ABC-1"], "invalid issue key"),
        (["issues", "transition", "ABC-1", "ABC-", "--id", "31"], "invalid issue key"),
        (["issues", "links", "create", "ABC-1", "Blocks", "x y", "--yes"], "invalid issue key"),
        (["projects", "get", "A/B?x=y"], "invalid project key 'A/B?x=y'"),
        (["projects", "get", ".."], "invalid project key"),
    ],
)
def test_usage_errors_exit_2_before_any_request(args: list[str], message: str) -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        result = CliInvoker().invoke(app, args)

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert message in result.stderr
    assert len(route.calls) == 0


@pytest.mark.parametrize(
    "args",
    [
        ["issues", "comments", "list", "../x"],
        ["issues", "transitions", "../x"],
        ["issues", "get", "../x"],
        ["projects", "get", "../x"],
    ],
)
def test_invalid_keys_exit_2_even_without_jira_config(jira_config: Path, args: list[str]) -> None:
    jira_config.write_text("profiles:\n  default: {}\n")
    result = CliInvoker().invoke(app, args)

    assert result.exit_code == 2, result.output
    assert "invalid" in result.stderr


def test_transition_rejects_an_invalid_piped_key_before_any_request() -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        result = invoke_cli(
            app, ["issues", "transition", "--stdin", "--id", "31", "--yes"], input="ABC-1\n../x\n"
        )

    assert result.exit_code == 2, result.output
    assert "invalid issue key '../x'" in result.stderr
    assert len(route.calls) == 0


@pytest.mark.parametrize(
    ("key", "path"),
    [("abc_2-7", "/rest/api/2/issue/ABC_2-7"), ("10001", "/rest/api/2/issue/10001")],
)
def test_issue_keys_accept_lowercase_and_numeric_ids(key: str, path: str) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.get(path).mock(return_value=httpx.Response(200, json=_issue("ABC_2-7")))
        result = CliInvoker().invoke(app, ["issues", "get", key, "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(route.calls) == 1


# --- write confirmation ------------------------------------------------------------

WRITES = {
    "create": (["issues", "create", "--project", "ABC", "--summary", "x"], "POST", "/issue"),
    "patch": (["issues", "patch", "ABC-1", "--summary", "x"], "PUT", "/issue/ABC-1"),
    "comment": (["issues", "comment", "ABC-1", "--body", "hi"], "POST", "/issue/ABC-1/comment"),
    "transition": (
        ["issues", "transition", "ABC-1", "--id", "31"],
        "POST",
        "/issue/ABC-1/transitions",
    ),
    "link": (["issues", "links", "create", "ABC-1", "Blocks", "ABC-2"], "POST", "/issueLink"),
}


def _mock_writes(mock: respx.MockRouter) -> respx.Route:
    """Mock the writes and the reads their previews make; return the write route."""
    mock.get("/rest/api/2/issue/ABC-1").mock(
        return_value=httpx.Response(200, json={"key": "ABC-1", "fields": {"summary": "old"}})
    )
    mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "31", "name": "Done"}]})
    )
    return mock.route(method__in=["POST", "PUT"]).mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ABC-1"})
    )


@pytest.fixture
def confirm_always(jira_config: Path) -> None:
    jira_config.write_text(jira_config.read_text() + "      confirm: always\n")


@pytest.mark.usefixtures("confirm_always")
@pytest.mark.parametrize("verb", sorted(WRITES))
def test_writes_require_yes_when_not_interactive(verb: str) -> None:
    args, _, _ = WRITES[verb]
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = _mock_writes(mock)
        result = invoke_cli(app, args)

    assert result.exit_code == 2, result.output
    assert f"error: {verb} requires --yes when not interactive" in result.stderr
    assert len(route.calls) == 0


@pytest.mark.usefixtures("confirm_always")
@pytest.mark.parametrize("verb", sorted(WRITES))
def test_writes_prompt_and_honour_a_decline(verb: str) -> None:
    args, method, path = WRITES[verb]
    backend = ScriptedPromptBackend(confirms=[False])
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = _mock_writes(mock)
        result = invoke_cli(app, args, terminal=True, prompt_backend=backend)

    assert result.exit_code == 1, result.output
    assert "cancelled; no changes made" in result.stderr
    assert f"{method} /rest/api/2{path}" in result.stderr
    assert backend.calls and backend.calls[0][0] == "confirm"
    assert len(route.calls) == 0


@pytest.mark.usefixtures("confirm_always")
@pytest.mark.parametrize("verb", sorted(WRITES))
def test_writes_proceed_after_confirmation(verb: str) -> None:
    args, _, _ = WRITES[verb]
    backend = ScriptedPromptBackend(confirms=[True])
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = _mock_writes(mock)
        result = invoke_cli(app, [*args, "--format", "json"], terminal=True, prompt_backend=backend)

    assert result.exit_code == 0, result.output
    assert len(route.calls) == 1
    assert json.loads(result.stdout)["action"] != "planned"


@pytest.mark.parametrize("verb", sorted(WRITES))
def test_dry_run_prints_the_request_and_wins_over_yes(verb: str) -> None:
    args, method, path = WRITES[verb]
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = _mock_writes(mock)
        result = invoke_cli(app, [*args, "--dry-run", "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(route.calls) == 0
    assert f"{method} /rest/api/2{path}" in result.stderr
    assert json.loads(result.stdout)["action"] == "planned"


def test_comment_dry_run_shows_the_body() -> None:
    result = invoke_cli(app, ["issues", "comment", "ABC-1", "--dry-run"], input="hello\n")

    assert result.exit_code == 0, result.output
    assert "  comment:\n    hello\n" in result.stderr


# --- stdin -------------------------------------------------------------------------


def _pipe(kind: str, *records: dict[str, object]) -> str:
    return "".join(
        json.dumps({"untaped": "1", "kind": kind, "record": record}) + "\n" for record in records
    )


def test_issue_get_reads_keys_from_pipe_records() -> None:
    with respx.mock(base_url=BASE) as mock:
        for key in ("ABC-1", "ABC-2"):
            mock.get(f"/rest/api/2/issue/{key}").mock(
                return_value=httpx.Response(200, json=_issue(key))
            )
        result = invoke_cli(
            app,
            ["issues", "get", "--stdin", "--format", "json"],
            input=_pipe("jira.issue", {"key": "ABC-1"}, {"key": "ABC-2"}),
        )

    assert result.exit_code == 0, result.output
    assert [row["key"] for row in json.loads(result.stdout)] == ["ABC-1", "ABC-2"]


def test_issue_get_several_keys_reports_each_failure() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-1").mock(
            return_value=httpx.Response(200, json=_issue("ABC-1"))
        )
        mock.get("/rest/api/2/issue/ABC-9").mock(return_value=httpx.Response(404))
        result = invoke_cli(app, ["issues", "get", "ABC-1", "ABC-9", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert [row["key"] for row in json.loads(result.stdout)] == ["ABC-1"]
    assert "error: ABC-9: issue not found: 'ABC-9'" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["issues", "get", "--stdin"],
        ["issues", "transition", "--stdin", "--id", "31", "--yes"],
    ],
)
def test_stdin_rejects_records_of_another_kind(args: list[str]) -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        result = invoke_cli(app, args, input=_pipe("jira.project", {"key": "ABC"}))

    assert result.exit_code == 2, result.output
    assert "jira.project" in result.stderr
    assert len(route.calls) == 0


def test_issue_transition_applies_to_every_piped_key() -> None:
    transitions = {"transitions": [{"id": "31", "name": "Done"}]}
    with respx.mock(base_url=BASE) as mock:
        posts = []
        for key in ("ABC-1", "ABC-2"):
            mock.get(f"/rest/api/2/issue/{key}/transitions").mock(
                return_value=httpx.Response(200, json=transitions)
            )
            posts.append(
                mock.post(f"/rest/api/2/issue/{key}/transitions").mock(
                    return_value=httpx.Response(204)
                )
            )
        result = invoke_cli(
            app,
            ["issues", "transition", "--stdin", "--to", "done", "--yes", "--format", "json"],
            input="ABC-1\nABC-2\n",
        )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [(row["key"], row["action"], row["transition_id"]) for row in rows] == [
        ("ABC-1", "transitioned", "31"),
        ("ABC-2", "transitioned", "31"),
    ]
    assert all(len(route.calls) == 1 for route in posts)


def test_issue_transition_unknown_name_lists_the_available_ones() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": "Done"}]})
        )
        result = invoke_cli(app, ["issues", "transition", "ABC-1", "--to", "Nope", "--yes"])

    assert result.exit_code == 1, result.output
    assert "transition not found: 'Nope'; known: Done" in result.stderr


# --- HTTP error mapping ------------------------------------------------------------


def test_missing_issue_is_a_not_found_error() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-9").mock(return_value=httpx.Response(404))
        result = invoke_cli(app, ["issues", "get", "ABC-9"])

    assert result.exit_code == 1, result.output
    assert "error: issue not found: 'ABC-9'" in result.stderr.splitlines()


def test_rejected_token_hints_at_config_set() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/myself").mock(return_value=httpx.Response(401))
        result = invoke_cli(app, ["whoami"])

    assert result.exit_code == 4, result.output
    assert result.stderr.endswith(
        "error: Jira rejected the token (HTTP 401)\n"
        "hint: run `untaped config set jira.token --prompt`\n"
    )


def test_rejected_token_is_an_auth_diagnostic_under_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/myself").mock(return_value=httpx.Response(401))
        result = invoke_cli(app, ["whoami", "--format", "json"])

    assert result.exit_code == 4, result.output
    record = json.loads(result.stderr.splitlines()[-1])
    assert record["message"] == "Jira rejected the token (HTTP 401)"
    assert (record["category"], record["system"]) == ("auth", "jira")
    assert record["hint"] == "run `untaped config set jira.token --prompt`"


@pytest.mark.parametrize(
    ("status", "category", "exit_code"),
    [(403, "permission", 4), (503, "unavailable", 5), (400, "invalid", 1)],
)
def test_http_failures_keep_their_category(
    monkeypatch: pytest.MonkeyPatch, status: int, category: str, exit_code: int
) -> None:
    monkeypatch.setattr("untaped.http._sleep", lambda _delay: None)
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(status))
        result = invoke_cli(app, ["issues", "get", "ABC-1", "--format", "json"])

    assert result.exit_code == exit_code, result.output
    record = json.loads(result.stderr.splitlines()[-1])
    assert (record["category"], record["system"]) == (category, "jira")


def test_missing_issue_is_a_not_found_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-9").mock(return_value=httpx.Response(404))
        result = invoke_cli(app, ["issues", "get", "ABC-9", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert json.loads(result.stderr.splitlines()[-1])["category"] == "not_found"


def test_fields_updated_but_forbidden_assignment_keeps_its_category() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.put("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(204))
        mock.put("/rest/api/2/issue/ABC-1/assignee").mock(return_value=httpx.Response(403))
        result = invoke_cli(
            app, ["issues", "patch", "ABC-1", "--summary", "x", "--assignee", "bob", "--yes"]
        )

    assert result.exit_code == 4, result.output
    assert result.stderr.endswith(
        "error: fields updated, but assigning failed: permission denied (HTTP 403)\n"
    )
