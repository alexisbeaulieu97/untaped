"""CLI tests for the jira command conventions (``docs/conventions.md``).

Covers the renamed commands and flags (old spellings stay as warning
aliases through the root), usage errors (exit 2), the write confirmation
contract (``--yes`` / ``--dry-run``), ``--stdin`` identifiers, and HTTP
error mapping.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from untaped import bootstrap
from untaped.capabilities.jira.cli import app
from untaped.capabilities.jira.settings import JiraSettings
from untaped.settings import get_settings, register_profile_settings
from untaped.testing import CliInvoker, ScriptedPromptBackend, invoke_cli

BASE = "https://jira.example.com"


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    bootstrap._clear_for_tests()
    register_profile_settings("jira", JiraSettings)
    get_settings.cache_clear()
    yield
    bootstrap._clear_for_tests()
    get_settings.cache_clear()


@pytest.fixture
def jira_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        f"profiles:\n  default:\n    jira:\n      base_url: {BASE}\n      token: jira_pat\n"
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    return cfg


def _root() -> object:
    return bootstrap.build_root_app(externals=[])


def _issue(key: str) -> dict[str, object]:
    return {"key": key, "self": f"{BASE}/rest/api/2/issue/{key}", "fields": {"summary": key}}


# --- renamed commands and flags -------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new"),
    [("me", "whoami"), ("issue", "issues"), ("project", "projects")],
)
def test_old_command_names_are_hidden_warning_aliases(
    jira_config: Path, old: str, new: str
) -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/rest/api/2/myself").mock(return_value=httpx.Response(200, json={"name": "a"}))
        mock.get("/rest/api/2/issue/ABC-1").mock(
            return_value=httpx.Response(200, json=_issue("ABC-1"))
        )
        mock.get("/rest/api/2/project").mock(return_value=httpx.Response(200, json=[]))
        args = {
            "me": ["jira", "me"],
            "issue": ["jira", "issue", "get", "ABC-1"],
            "project": ["jira", "project", "list"],
        }[old]
        result = invoke_cli(_root(), [*args, "--format", "json"])  # type: ignore[arg-type]

    assert result.exit_code == 0, result.output
    assert f"warning: `{old}` is deprecated and will be removed in 7.0; use `{new}`" in (
        result.stderr
    )
    help_text = invoke_cli(_root(), ["jira", "--help"]).stdout  # type: ignore[arg-type]
    assert new in help_text
    for hidden in ("me ", "issue ", "project ", "board ", "sprint "):
        assert f" {hidden}" not in help_text


def test_old_board_and_sprint_groups_still_work(jira_config: Path) -> None:
    page = {"startAt": 0, "maxResults": 50, "isLast": True, "values": []}
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/agile/1.0/board").mock(return_value=httpx.Response(200, json=page))
        mock.get("/rest/agile/1.0/board/7/sprint").mock(return_value=httpx.Response(200, json=page))
        boards = invoke_cli(_root(), ["jira", "board", "list", "--format", "json"])  # type: ignore[arg-type]
        sprints = invoke_cli(
            _root(),  # type: ignore[arg-type]
            ["jira", "sprint", "list", "--board-id", "7", "--format", "json"],
        )

    for result, old in ((boards, "board"), (sprints, "sprint")):
        assert result.exit_code == 0, result.output
        assert f"`{old}` is deprecated" in result.stderr


def test_issue_edit_and_field_flags_alias_patch_and_set(jira_config: Path) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.put("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(204))
        result = invoke_cli(
            _root(),  # type: ignore[arg-type]
            [
                "jira",
                "issue",
                "edit",
                "ABC-1",
                "--field",
                "summary=new",
                "--json-field",
                'labels=["a"]',
                "--yes",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    for old, new in (("issue", "issues"), ("edit", "patch"), ("--field", "--set")):
        assert f"`{old}` is deprecated and will be removed in 7.0; use `{new}`" in result.stderr
    assert "`--json-field` is deprecated" in result.stderr
    assert json.loads(route.calls[0].request.content) == {
        "fields": {"summary": "new", "labels": ["a"]}
    }
    assert json.loads(result.stdout)["action"] == "updated"


def test_create_field_flags_alias_set(jira_config: Path) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/issue").mock(
            return_value=httpx.Response(201, json={"id": "1", "key": "ABC-1"})
        )
        result = invoke_cli(
            _root(),  # type: ignore[arg-type]
            ["jira", "issues", "create", "--yes", "--project", "ABC", "--field", "summary=x"],
        )

    assert result.exit_code == 0, result.output
    assert "`--field` is deprecated" in result.stderr
    assert json.loads(route.calls[0].request.content)["fields"]["summary"] == "x"


# --- usage errors ----------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["issues", "transition", "ABC-1", "--yes"],
        ["issues", "transition", "ABC-1", "--yes", "--id", "1", "--to", "Done"],
        ["issues", "assigned", "--jql", "  "],
        ["sprints", "list"],
        ["issues", "search", "--limit", "0"],
    ],
)
def test_usage_errors_exit_2_before_any_request(jira_config: Path, args: list[str]) -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        result = CliInvoker().invoke(app, args)

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert len(route.calls) == 0


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
    return mock.route(method__in=["POST", "PUT"]).mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ABC-1"})
    )


@pytest.mark.parametrize("verb", sorted(WRITES))
def test_writes_require_yes_when_not_interactive(jira_config: Path, verb: str) -> None:
    args, _, _ = WRITES[verb]
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = _mock_writes(mock)
        result = invoke_cli(app, args)

    assert result.exit_code == 2, result.output
    assert f"error: {verb} requires --yes when not interactive" in result.stderr
    assert len(route.calls) == 0


@pytest.mark.parametrize("verb", sorted(WRITES))
def test_writes_prompt_and_honour_a_decline(jira_config: Path, verb: str) -> None:
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


@pytest.mark.parametrize("verb", sorted(WRITES))
def test_writes_proceed_after_confirmation(jira_config: Path, verb: str) -> None:
    args, _, _ = WRITES[verb]
    backend = ScriptedPromptBackend(confirms=[True])
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = _mock_writes(mock)
        result = invoke_cli(app, [*args, "--format", "json"], terminal=True, prompt_backend=backend)

    assert result.exit_code == 0, result.output
    assert len(route.calls) == 1
    assert json.loads(result.stdout)["action"] != "planned"


@pytest.mark.parametrize("verb", sorted(WRITES))
def test_dry_run_prints_the_request_and_wins_over_yes(jira_config: Path, verb: str) -> None:
    args, method, path = WRITES[verb]
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = _mock_writes(mock)
        result = invoke_cli(app, [*args, "--dry-run", "--yes", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(route.calls) == 0
    assert f"{method} /rest/api/2{path}" in result.stderr
    assert json.loads(result.stdout)["action"] == "planned"


def test_comment_dry_run_shows_the_body(jira_config: Path) -> None:
    result = invoke_cli(app, ["issues", "comment", "ABC-1", "--dry-run"], input="hello\n")

    assert result.exit_code == 0, result.output
    assert '"body": "hello"' in result.stderr


# --- stdin -------------------------------------------------------------------------


def _pipe(kind: str, *records: dict[str, object]) -> str:
    return "".join(
        json.dumps({"untaped": "1", "kind": kind, "record": record}) + "\n" for record in records
    )


def test_issue_get_reads_keys_from_pipe_records(jira_config: Path) -> None:
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


def test_issue_get_several_keys_reports_each_failure(jira_config: Path) -> None:
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
def test_stdin_rejects_records_of_another_kind(jira_config: Path, args: list[str]) -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={}))
        result = invoke_cli(app, args, input=_pipe("jira.project", {"key": "ABC"}))

    assert result.exit_code == 2, result.output
    assert "jira.project" in result.stderr
    assert len(route.calls) == 0


def test_issue_transition_applies_to_every_piped_key(jira_config: Path) -> None:
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


def test_issue_transition_unknown_name_lists_the_available_ones(jira_config: Path) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": "Done"}]})
        )
        result = invoke_cli(app, ["issues", "transition", "ABC-1", "--to", "Nope", "--yes"])

    assert result.exit_code == 1, result.output
    assert "transition not found: 'Nope'; known: Done" in result.stderr


# --- HTTP error mapping ------------------------------------------------------------


def test_missing_issue_is_a_not_found_error(jira_config: Path) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ABC-9").mock(return_value=httpx.Response(404))
        result = invoke_cli(app, ["issues", "get", "ABC-9"])

    assert result.exit_code == 1, result.output
    assert "error: issue not found: 'ABC-9'" in result.stderr.splitlines()


def test_rejected_token_hints_at_config_set(jira_config: Path) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/myself").mock(return_value=httpx.Response(401))
        result = invoke_cli(app, ["whoami"])

    assert result.exit_code == 1, result.output
    assert "hint: run `untaped config set jira.token --prompt`" in result.stderr
