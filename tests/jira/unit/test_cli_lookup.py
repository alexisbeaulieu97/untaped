"""CLI tests for Jira project, board, and sprint lookup helpers."""

from __future__ import annotations

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


def test_project_list_outputs_project_keys(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/project").mock(
            return_value=httpx.Response(200, json=[{"id": "10000", "key": "ABC", "name": "App"}])
        )
        result = CliInvoker().invoke(
            app, ["project", "list", "--format", "raw", "--columns", "key"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC"


def test_project_get_outputs_one_project(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/project/ABC").mock(
            return_value=httpx.Response(200, json={"id": "10000", "key": "ABC", "name": "App"})
        )
        result = CliInvoker().invoke(
            app, ["project", "get", "ABC", "--format", "raw", "--columns", "key"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC"


def test_project_get_missing_key_is_usage_error() -> None:
    result = CliInvoker().invoke(app, ["project", "get"])

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "requires an argument" in result.stderr


def test_board_list_filters_by_project(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.get("/rest/agile/1.0/board").mock(
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
        result = CliInvoker().invoke(
            app, ["board", "list", "--project", "ABC", "--format", "raw", "--columns", "id"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "7"
    assert route.calls[0].request.url.params["projectKeyOrId"] == "ABC"


def test_sprint_list_uses_configured_default_board(jira_config: Path) -> None:
    jira_config.write_text(
        "profiles:\n"
        "  default:\n"
        "    jira:\n"
        "      base_url: https://jira.example.com\n"
        "      token: jira_pat\n"
        "      default_board_id: 7\n"
    )
    with respx.mock(base_url="https://jira.example.com") as mock:
        route = mock.get("/rest/agile/1.0/board/7/sprint").mock(
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
        result = CliInvoker().invoke(
            app, ["sprint", "list", "--state", "active", "--format", "raw", "--columns", "id"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "20"
    assert route.calls[0].request.url.params["state"] == "active"


def test_project_list_empty_guides_with_stderr_hint(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/project").mock(return_value=httpx.Response(200, json=[]))
        result = CliInvoker().invoke(app, ["project", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No projects are visible to you" in result.stderr


def test_project_list_empty_json_stays_pipe_clean(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/project").mock(return_value=httpx.Response(200, json=[]))
        result = CliInvoker().invoke(app, ["project", "list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "[]"
    assert "No projects are visible to you" not in result.stderr


def test_project_list_reports_progress_on_stderr(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/api/2/project").mock(
            return_value=httpx.Response(200, json=[{"id": "10000", "key": "ABC", "name": "App"}])
        )
        result = CliInvoker().invoke(
            app, ["project", "list", "--format", "raw", "--columns", "key"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ABC"
    assert "Listing projects" in result.stderr
    assert "Listing projects" not in result.stdout


def test_board_list_empty_guides_with_stderr_hint(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/agile/1.0/board").mock(
            return_value=httpx.Response(
                200,
                json={"startAt": 0, "maxResults": 50, "isLast": True, "values": []},
            )
        )
        result = CliInvoker().invoke(app, ["board", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No boards match the filter" in result.stderr


def test_sprint_list_empty_guides_with_stderr_hint(jira_config: Path) -> None:
    with respx.mock(base_url="https://jira.example.com") as mock:
        mock.get("/rest/agile/1.0/board/7/sprint").mock(
            return_value=httpx.Response(
                200,
                json={"startAt": 0, "maxResults": 50, "isLast": True, "values": []},
            )
        )
        result = CliInvoker().invoke(app, ["sprint", "list", "--board-id", "7"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No sprints found for this board" in result.stderr
