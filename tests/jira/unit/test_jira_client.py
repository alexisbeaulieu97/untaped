"""Unit tests for the Jira REST client: auth, pagination, retry, and error text."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from untaped.capabilities.jira.errors import JiraError
from untaped.capabilities.jira.infrastructure import JiraClient
from untaped.capabilities.jira.settings import JiraSettings
from untaped.capability_api import ConfigError, HttpStatusError

BASE = "https://jira.example.com"


def _settings(**overrides: object) -> JiraSettings:
    return JiraSettings.model_validate({"base_url": BASE, "token": "jira_pat", **overrides})


def _search_page(start: int, total: int, *keys: str) -> httpx.Response:
    issues = [{"key": key, "fields": {}} for key in keys]
    body = {"startAt": start, "maxResults": 1, "total": total, "issues": issues}
    return httpx.Response(200, json=body)


def test_client_sends_pat_bearer_header_and_joins_api_prefix() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "alexis"})
        )
        with JiraClient(_settings(api_prefix="/rest/api/2/")) as client:
            assert client.me()["name"] == "alexis"

    assert route.calls[0].request.headers["authorization"] == "Bearer jira_pat"
    assert route.calls[0].request.headers["accept"] == "application/json"


@pytest.mark.parametrize(
    ("config", "setting"),
    [
        (JiraSettings(token=SecretStr("jira_pat")), r"jira\.base_url"),
        (JiraSettings(base_url=BASE, token=SecretStr("   ")), r"jira\.token"),
    ],
)
def test_client_requires_base_url_and_non_blank_token(config: JiraSettings, setting: str) -> None:
    with pytest.raises(ConfigError, match=setting):
        JiraClient(config)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [({"api_prefix": "rest/api/2"}, "must start with '/'"), ({"assigned_jql": " "}, "blank")],
)
def test_settings_reject_relative_prefixes_and_blank_assigned_jql(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(**overrides)


def test_search_issues_posts_jql_and_honours_limit() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/search").mock(return_value=_search_page(0, 1, "ABC-1"))
        with JiraClient(_settings()) as client:
            rows = list(client.search_issues("project = ABC", limit=1))

    assert [row["key"] for row in rows] == ["ABC-1"]
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["jql"] == "project = ABC"
    assert request_json["maxResults"] == 1


def test_search_issues_walks_start_at_pages_until_total() -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/search").mock(
            side_effect=[_search_page(0, 2, "ABC-1"), _search_page(1, 2, "ABC-2")]
        )
        with JiraClient(_settings(page_size=1)) as client:
            rows = list(client.search_issues("project = ABC"))

    assert [row["key"] for row in rows] == ["ABC-1", "ABC-2"]
    assert json.loads(route.calls[1].request.content)["startAt"] == 1


def test_search_retries_a_429_although_it_is_a_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """JQL search opts its idempotent POST into retry; mutating POSTs never retry."""
    monkeypatch.setattr("untaped.http._sleep", lambda _delay: None)
    with respx.mock(base_url=BASE) as mock:
        search = mock.post("/rest/api/2/search").mock(
            side_effect=[httpx.Response(429), _search_page(0, 1, "ABC-1")]
        )
        create = mock.post("/rest/api/2/issue").mock(return_value=httpx.Response(429))
        with JiraClient(_settings()) as client:
            rows = list(client.search_issues("project = ABC"))
            with pytest.raises(JiraError):
                client.create_issue({"fields": {}})

    assert [row["key"] for row in rows] == ["ABC-1"]
    assert search.call_count == 2
    assert create.call_count == 1


@pytest.mark.parametrize(
    ("path", "list_rows"),
    [
        ("/rest/agile/1.0/board", lambda client: client.list_boards()),
        ("/rest/agile/1.0/board/7/sprint", lambda client: client.list_sprints(7)),
    ],
)
def test_agile_lists_continue_while_is_last_is_false_on_a_short_page(
    path: str, list_rows: object
) -> None:
    def page(start: int, is_last: bool, *ids: int) -> httpx.Response:
        values = [{"id": value} for value in ids]
        body = {"startAt": start, "maxResults": 3, "isLast": is_last, "values": values}
        return httpx.Response(200, json=body)

    with respx.mock(base_url=BASE) as mock:
        route = mock.get(path).mock(side_effect=[page(0, False, 1, 2), page(2, True, 3)])
        with JiraClient(_settings(page_size=3)) as client:
            rows = list(list_rows(client))  # type: ignore[operator]

    assert [row["id"] for row in rows] == [1, 2, 3]
    assert route.calls[1].request.url.params["startAt"] == "2"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {"errorMessages": ["Bad project."], "errors": {"summary": "You must specify one."}},
            "Bad project.; summary: You must specify one.",
        ),
        ("<html>proxy error</html>", "HTTP 400"),
    ],
)
def test_client_surfaces_jira_error_messages(body: object, expected: str) -> None:
    content = json.dumps(body) if isinstance(body, dict) else str(body)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/rest/api/2/issue").mock(return_value=httpx.Response(400, content=content))
        with JiraClient(_settings()) as client, pytest.raises(JiraError) as caught:
            client.create_issue({"fields": {}})

    assert expected in str(caught.value)
    assert not isinstance(caught.value, HttpStatusError)


def test_client_maps_errors_raised_while_paginating() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/agile/1.0/board").mock(return_value=httpx.Response(403))
        with JiraClient(_settings()) as client, pytest.raises(JiraError) as caught:
            list(client.list_boards())

    assert "permission denied" in str(caught.value)
