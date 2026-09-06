"""CLI tests for ``--format pipe`` envelope tagging across jira commands.

Each producer command should tag its emitted records with a namespaced
``kind`` hint so a downstream untaped command can recognise the stream.
"""

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


def test_me_pipe_tags_user(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis", "displayName": "Alexis"})
        )
        result = CliInvoker().invoke(app, ["me", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["untaped"] == "1"
    assert envelope["kind"] == "jira.user"
    assert envelope["record"]["name"] == "alexis"


def test_issue_search_pipe_tags_issue(jira_config: Path) -> None:
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
            app, ["issue", "search", "--project", "ABC", "--format", "pipe"]
        )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["untaped"] == "1"
    assert envelope["kind"] == "jira.issue"
    assert envelope["record"]["key"] == "ABC-1"


def test_issue_comment_pipe_tags_comment(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(201, json={"id": "700"})
        )
        result = CliInvoker().invoke(
            app, ["issue", "comment", "ABC-1", "--format", "pipe"], input="hi\n"
        )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "jira.comment"


def test_issue_transitions_pipe_tags_transition(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/issue/ABC-1/transitions").mock(
            return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": "Done"}]})
        )
        result = CliInvoker().invoke(app, ["issue", "transitions", "ABC-1", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "jira.transition"


def test_project_list_pipe_tags_project(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/project").mock(
            return_value=httpx.Response(200, json=[{"id": "10000", "key": "ABC", "name": "App"}])
        )
        result = CliInvoker().invoke(app, ["project", "list", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "jira.project"
    assert envelope["record"]["key"] == "ABC"


def test_board_list_pipe_tags_board(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/agile/1.0/board").mock(
            return_value=httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "maxResults": 50,
                    "isLast": True,
                    "values": [{"id": 7, "name": "ABC Board", "type": "scrum"}],
                },
            )
        )
        result = CliInvoker().invoke(app, ["board", "list", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "jira.board"


def test_sprint_list_pipe_tags_sprint(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/agile/1.0/board/7/sprint").mock(
            return_value=httpx.Response(
                200,
                json={
                    "startAt": 0,
                    "maxResults": 50,
                    "isLast": True,
                    "values": [{"id": 20, "name": "Sprint 20", "state": "active"}],
                },
            )
        )
        result = CliInvoker().invoke(app, ["sprint", "list", "--board-id", "7", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "jira.sprint"


def test_issue_create_pipe_tags_issue(jira_config: Path) -> None:
    """A mutation result is tagged as the entity it affects (jira.issue), not an outcome."""
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/issue").mock(
            return_value=httpx.Response(
                201, json={"id": "10001", "key": "ABC-1", "self": "https://jira.example.com/ABC-1"}
            )
        )
        result = CliInvoker().invoke(
            app, ["issue", "create", "--project", "ABC", "--summary", "x", "--format", "pipe"]
        )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "jira.issue"
    assert envelope["record"]["key"] == "ABC-1"


def test_issue_transition_pipe_tags_issue(jira_config: Path) -> None:
    """`issue transition` (mutation) also tags jira.issue, while `issue
    transitions` (the list) tags jira.transition — the singular/plural split."""
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/issue/ABC-1/transitions").mock(return_value=httpx.Response(204))
        result = CliInvoker().invoke(
            app, ["issue", "transition", "ABC-1", "--id", "31", "--format", "pipe"]
        )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "jira.issue"
