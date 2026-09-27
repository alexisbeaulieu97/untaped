"""CLI tests for how board and sprint lookups pass filters and settings to Jira."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from untaped.capabilities.jira.cli import app
from untaped.testing import CliInvoker

BASE = "https://jira.example.com"
_PAGE = {"startAt": 0, "maxResults": 50, "isLast": True, "values": [{"id": 7, "name": "B"}]}


def test_board_list_filters_by_project() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/rest/agile/1.0/board").mock(return_value=httpx.Response(200, json=_PAGE))
        result = CliInvoker().invoke(
            app, ["boards", "list", "--project", "ABC", "--format", "raw", "--columns", "id"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "7"
    assert route.calls[0].request.url.params["projectKeyOrId"] == "ABC"


def test_sprint_list_uses_configured_default_board(jira_config: Path) -> None:
    jira_config.write_text(jira_config.read_text() + "      default_board_id: 7\n")
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/rest/agile/1.0/board/7/sprint").mock(
            return_value=httpx.Response(200, json=_PAGE)
        )
        result = CliInvoker().invoke(app, ["sprints", "list", "--state", "active"])

    assert result.exit_code == 0, result.output
    assert route.calls[0].request.url.params["state"] == "active"
