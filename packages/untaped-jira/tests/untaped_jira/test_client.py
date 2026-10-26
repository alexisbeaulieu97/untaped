"""Unit tests for the Jira REST client: auth, pagination, retry, and error text."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx
import pytest
import respx
from pydantic import ValidationError

from jira_test_support import BASE
from untaped.sdk import ConfigError, HttpStatusError, HttpTransportError
from untaped.sdk.testing import Install
from untaped_jira.client import JiraClient
from untaped_jira.errors import JiraError
from untaped_jira.parse import ISSUE_DETAIL_FIELDS
from untaped_jira.plugin import Jira
from untaped_jira.settings import TOKEN, JiraServer, JiraSettings

#: ``connect(settings)``: a client built as a jira command builds it (the ``connect`` fixture).
Connect = Callable[[JiraSettings], JiraClient]


@pytest.fixture
def connect(install: Callable[..., Install], monkeypatch: pytest.MonkeyPatch) -> Iterator[Connect]:
    monkeypatch.setenv("JIRA_API_TOKEN", "jira_pat")
    with install(Jira).running() as run:
        yield lambda settings: JiraClient(settings, http=run.http)


def _settings(**overrides: object) -> JiraSettings:
    return JiraSettings.model_validate({"server": {"url": BASE}, **overrides})


def _search_page(start: int, total: int, *keys: str) -> httpx.Response:
    issues = [{"key": key, "fields": {}} for key in keys]
    body = {"startAt": start, "maxResults": 1, "total": total, "issues": issues}
    return httpx.Response(200, json=body)


def test_client_sends_pat_bearer_header_and_joins_api_prefix(connect: Connect) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "jdoe"})
        )
        with connect(_settings(api_prefix="/rest/api/2/")) as client:
            assert client.me()["name"] == "jdoe"

    assert route.calls[0].request.headers["authorization"] == "Bearer jira_pat"
    assert route.calls[0].request.headers["accept"] == "application/json"


def test_client_requires_a_server(connect: Connect) -> None:
    with pytest.raises(ConfigError, match=r"jira\.server is not set"):
        connect(JiraSettings())


@pytest.mark.parametrize("token", ["", "   "])
def test_a_blank_token_is_not_a_token(
    install: Callable[..., Install], monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setenv("JIRA_API_TOKEN", token)
    with (
        install(Jira).running() as run,
        pytest.raises(ConfigError, match="no jira token") as caught,
    ):
        JiraClient(_settings(), http=run.http)
    assert "untaped auth login jira" in (caught.value.hint or "")


def test_the_token_is_read_from_jira_api_token(
    install: Callable[..., Install], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIRA_API_TOKEN", "from-env")
    with install(Jira).running():
        assert TOKEN.get(JiraServer(url=BASE)).get_secret_value() == "from-env"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [({"api_prefix": "rest/api/2"}, "must start with '/'"), ({"assigned_jql": " "}, "blank")],
)
def test_settings_reject_relative_prefixes_and_blank_assigned_jql(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(**overrides)


def test_search_issues_posts_jql_and_honours_limit(connect: Connect) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/search").mock(return_value=_search_page(0, 1, "ABC-1"))
        with connect(_settings()) as client:
            rows = list(client.search_issues("project = ABC", limit=1))

    assert [row["key"] for row in rows] == ["ABC-1"]
    request_json = json.loads(route.calls[0].request.content)
    assert request_json["jql"] == "project = ABC"
    assert request_json["maxResults"] == 1


def test_search_issues_walks_start_at_pages_until_total(connect: Connect) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/search").mock(
            side_effect=[_search_page(0, 2, "ABC-1"), _search_page(1, 2, "ABC-2")]
        )
        with connect(_settings(page_size=1)) as client:
            rows = list(client.search_issues("project = ABC"))

    assert [row["key"] for row in rows] == ["ABC-1", "ABC-2"]
    assert json.loads(route.calls[1].request.content)["startAt"] == 1


@pytest.mark.usefixtures("clock")  # the back-off waits on it
def test_search_retries_a_429_although_it_is_a_post(connect: Connect) -> None:
    """JQL search opts its idempotent POST into retry; mutating POSTs never retry."""
    with respx.mock(base_url=BASE) as mock:
        search = mock.post("/rest/api/2/search").mock(
            side_effect=[httpx.Response(429), _search_page(0, 1, "ABC-1")]
        )
        create = mock.post("/rest/api/2/issue").mock(return_value=httpx.Response(429))
        with connect(_settings()) as client:
            rows = list(client.search_issues("project = ABC"))
            with pytest.raises(JiraError):
                client.create_issue({"fields": {}})

    assert [row["key"] for row in rows] == ["ABC-1"]
    assert search.call_count == 2
    assert create.call_count == 1


@pytest.mark.parametrize(
    ("path", "send"),
    [
        ("/rest/api/2/issue/ABC-1", lambda client: client.edit_issue("ABC-1", {"update": {}})),
        (
            "/rest/api/2/issue/ABC-1/assignee",
            lambda client: client.assign_issue("ABC-1", {"name": "ada"}),
        ),
    ],
)
@pytest.mark.parametrize("answer", [429, 503])
@pytest.mark.usefixtures("clock")  # the back-off waits on it
def test_patch_puts_are_sent_once(
    path: str, send: Callable[[JiraClient], object], answer: int, connect: Connect
) -> None:
    """A patch's PUT can carry an ``update`` add, so a retry could apply it twice."""
    with respx.mock(base_url=BASE) as mock:
        put = mock.put(path).mock(return_value=httpx.Response(answer))
        with connect(_settings()) as client, pytest.raises(JiraError):
            send(client)

    assert put.call_count == 1


@pytest.mark.usefixtures("clock")  # the back-off waits on it
def test_patch_put_is_not_resent_after_a_dropped_connection(connect: Connect) -> None:
    """Jira may have applied a PUT whose answer never arrived; only a failed connect is retried."""
    with respx.mock(base_url=BASE) as mock:
        dropped = mock.put("/rest/api/2/issue/ABC-1").mock(side_effect=httpx.ReadTimeout)
        unreached = mock.put("/rest/api/2/issue/ABC-1/assignee").mock(
            side_effect=[httpx.ConnectError("refused"), httpx.Response(204)]
        )
        with connect(_settings()) as client:
            with pytest.raises(HttpTransportError):
                client.edit_issue("ABC-1", {"update": {}})
            client.assign_issue("ABC-1", {"name": "ada"})

    assert dropped.call_count == 1
    assert unreached.call_count == 2


@pytest.mark.parametrize(
    ("path", "list_rows"),
    [
        ("/rest/agile/1.0/board", lambda client: client.list_boards()),
        ("/rest/agile/1.0/board/7/sprint", lambda client: client.list_sprints(7)),
    ],
)
def test_agile_lists_continue_while_is_last_is_false_on_a_short_page(
    path: str, list_rows: object, connect: Connect
) -> None:
    def page(start: int, is_last: bool, *ids: int) -> httpx.Response:
        values = [{"id": value} for value in ids]
        body = {"startAt": start, "maxResults": 3, "isLast": is_last, "values": values}
        return httpx.Response(200, json=body)

    with respx.mock(base_url=BASE) as mock:
        route = mock.get(path).mock(side_effect=[page(0, False, 1, 2), page(2, True, 3)])
        with connect(_settings(page_size=3)) as client:
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
def test_client_surfaces_jira_error_messages(body: object, expected: str, connect: Connect) -> None:
    content = json.dumps(body) if isinstance(body, dict) else str(body)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/rest/api/2/issue").mock(return_value=httpx.Response(400, content=content))
        with connect(_settings()) as client, pytest.raises(JiraError) as caught:
            client.create_issue({"fields": {}})

    assert expected in str(caught.value)
    assert not isinstance(caught.value, HttpStatusError)


def test_client_maps_errors_raised_while_paginating(connect: Connect) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/agile/1.0/board").mock(return_value=httpx.Response(403))
        with connect(_settings()) as client, pytest.raises(JiraError) as caught:
            list(client.list_boards())

    assert "permission denied" in str(caught.value)


def test_client_percent_encodes_path_values_into_one_segment(connect: Connect) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={"key": "X"}))
        with connect(_settings()) as client:
            client.get_project("A/B?x=y")
            client.get_issue("..")

    assert [call.request.url.raw_path for call in route.calls] == [
        b"/rest/api/2/project/A%2FB%3Fx%3Dy",
        b"/rest/api/2/issue/%2E%2E?fields="
        + b"%2C".join(field.encode() for field in ISSUE_DETAIL_FIELDS),
    ]
