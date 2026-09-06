"""Single-entity ``emit()`` contract: ``--format json`` emits a bare object
``{…}`` (not a one-element array ``[{…}]``), plus the JQL search's opt-in retry.
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


def test_me_json_emits_bare_object(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis", "displayName": "Alexis"})
        )
        result = CliInvoker().invoke(app, ["me", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["name"] == "alexis"


def test_issue_get_json_emits_bare_object(jira_config: Path) -> None:
    body = {"key": "ABC-1", "fields": {"summary": "Fix deploy"}}
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/issue/ABC-1").mock(return_value=httpx.Response(200, json=body))
        result = CliInvoker().invoke(app, ["issue", "get", "ABC-1", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["key"] == "ABC-1"


def test_issue_create_json_emits_bare_object(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/issue").mock(
            return_value=httpx.Response(201, json={"id": "10001", "key": "ABC-1"})
        )
        result = CliInvoker().invoke(
            app,
            [
                "issue",
                "create",
                "--project",
                "ABC",
                "--issue-type",
                "Bug",
                "--summary",
                "x",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["key"] == "ABC-1"


def test_issue_comment_json_emits_bare_object(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/issue/ABC-1/comment").mock(
            return_value=httpx.Response(201, json={"id": "5", "body": "hi"})
        )
        result = CliInvoker().invoke(
            app, ["issue", "comment", "ABC-1", "--body", "hi", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)


def test_issue_transition_json_emits_bare_object(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.post("/rest/api/2/issue/ABC-1/transitions").mock(return_value=httpx.Response(204))
        result = CliInvoker().invoke(
            app, ["issue", "transition", "ABC-1", "--id", "31", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)


def test_project_get_json_emits_bare_object(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/project/ABC").mock(
            return_value=httpx.Response(200, json={"id": "10000", "key": "ABC", "name": "App"})
        )
        result = CliInvoker().invoke(app, ["project", "get", "ABC", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["key"] == "ABC"


def test_search_retries_429_on_idempotent_post(
    jira_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The JQL search POST opts into retry, so a transient 429 is retried
    rather than surfaced — unlike the tool's mutating POSTs."""
    monkeypatch.setattr("untaped.http._sleep", lambda _delay: None)
    ok = httpx.Response(
        200,
        json={
            "startAt": 0,
            "maxResults": 50,
            "total": 1,
            "issues": [{"key": "ABC-1", "fields": {"summary": "Fix deploy"}}],
        },
    )
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/search").mock(side_effect=[httpx.Response(429), ok])
        result = CliInvoker().invoke(
            app,
            ["issue", "search", "--project", "ABC", "--format", "raw", "--columns", "key"],
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC-1"
    assert route.call_count == 2


def test_create_429_is_not_retried(jira_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The safety asymmetry: a mutating POST (issue create) is never retried,
    so a transient 429 surfaces immediately rather than risking a double-create."""
    monkeypatch.setattr("untaped.http._sleep", lambda _delay: None)
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.post("/rest/api/2/issue").mock(
            side_effect=[httpx.Response(429), httpx.Response(201, json={"key": "ABC-1"})]
        )
        result = CliInvoker().invoke(
            app, ["issue", "create", "--project", "ABC", "--issue-type", "Bug", "--summary", "x"]
        )

    assert result.exit_code != 0
    assert route.call_count == 1
