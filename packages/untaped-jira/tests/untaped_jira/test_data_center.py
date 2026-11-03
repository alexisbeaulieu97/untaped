"""The Data Center adapter, through jira's port, per recorded version.

Each port method reads its recorded answer into jira's records. The adapter
sends the token, pages with ``startAt`` (and Agile's ``isLast``), retries
only the search's POST, sends a patch's PUTs once, encodes path values as
one segment and turns Jira's error statuses into jira's errors. Jira's own
404 stays "not found"; a 404 without Jira's error is a mismatch.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import respx

from jira_test_support import BASE, Invoke, Settings, recorded_answer
from untaped.sdk import ConfigError, HttpStatusError, HttpTransportError
from untaped.sdk.network import ApiMismatch
from untaped.sdk.testing import Install
from untaped_jira.errors import JiraError
from untaped_jira.parse import ISSUE_DETAIL_FIELDS
from untaped_jira.plugin import Jira
from untaped_jira.ports import JiraApi
from untaped_jira.server import JiraVersion
from untaped_jira.server_api import SERVER_API
from untaped_jira.settings import TOKEN, JiraServer, JiraSettings

#: ``port(**settings)``: jira's port for those settings, in a running install.
Port = Callable[..., JiraApi]


@pytest.fixture
def port(
    install: Callable[..., Install], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Port]:
    monkeypatch.setenv("JIRA_API_TOKEN", "jira_pat")
    with install(Jira, settings=JiraSettings(**settings)).running():
        yield lambda **overrides: SERVER_API.port(JiraSettings(**(settings | overrides)))


def _answer(version: JiraVersion, name: str) -> httpx.Response:
    return httpx.Response(200, json=recorded_answer(version, name))


def _search_page(start: int, total: int, *keys: str) -> httpx.Response:
    issues = [{"key": key, "fields": {}} for key in keys]
    body = {"startAt": start, "maxResults": 1, "total": total, "issues": issues}
    return httpx.Response(200, json=body)


# ---- each method reads its recorded answer --------------------------------------------


@respx.mock(base_url=BASE)
def test_reads_turn_recorded_answers_into_records(
    respx_mock: respx.MockRouter, port: Port, server_version: JiraVersion
) -> None:
    for path, name in [
        ("/rest/api/2/myself", "myself"),
        ("/rest/api/2/issue/ACME-1", "issue"),
        ("/rest/api/2/issue/ACME-1/comment", "comments"),
        ("/rest/api/2/issue/ACME-1/transitions", "transitions"),
        ("/rest/api/2/project", "projects"),
        ("/rest/api/2/project/ACME", "project"),
        ("/rest/agile/1.0/board", "boards"),
        ("/rest/agile/1.0/board/7/sprint", "sprints"),
    ]:
        respx_mock.get(path).mock(return_value=_answer(server_version, name))
    respx_mock.post("/rest/api/2/search").mock(return_value=_answer(server_version, "search"))
    jira = port()

    assert jira.me().display_name == "Alice Example"
    issue = jira.issue("ACME-1")
    assert (issue.status, issue.assignee, issue.reporter) == (
        "In Progress",
        "Alice Example",
        "Bob Example",
    )
    assert issue.url == f"{BASE}/browse/ACME-1"
    assert [(link.key, link.relation) for link in issue.links or []] == [("ACME-2", "blocks")]
    assert [row.key for row in jira.search("project = ACME", limit=None)] == ["ACME-1", "ACME-2"]
    assert [each.body for each in jira.comments("ACME-1")] == ["Keys rotated on staging."]
    assert [(t.name, t.to_status) for t in jira.transitions("ACME-1")] == [
        ("Start Progress", "In Progress"),
        ("Done", "Done"),
    ]
    assert [project.key for project in jira.projects()] == ["ACME"]
    assert jira.project("ACME").name == "Acme"
    assert [
        board.name for board in jira.boards(project=None, name=None, board_type=None, limit=None)
    ] == ["ACME board"]
    assert [sprint.name for sprint in jira.sprints(7, state=None, limit=None)] == ["Sprint 42"]


@respx.mock(base_url=BASE)
def test_current_values_and_transitions_come_from_one_read(
    respx_mock: respx.MockRouter, port: Port, server_version: JiraVersion
) -> None:
    def issue(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = dict(recorded_answer(server_version, "issue"))
        body |= recorded_answer(server_version, "transitions")
        return httpx.Response(200, json=body)

    route = respx_mock.get("/rest/api/2/issue/ACME-1").mock(side_effect=issue)
    current = port().current("ACME-1", ["status", "resolution"], transitions=True)

    assert current.fields["status"]["name"] == "In Progress"
    assert [t.id for t in current.transitions] == ["21", "31"]
    params = route.calls.last.request.url.params
    assert (params["fields"], params["expand"], route.call_count) == (
        "status,resolution",
        "transitions",
        1,
    )


@respx.mock(base_url=BASE)
def test_writes_send_jiras_bodies_and_read_its_answers(
    respx_mock: respx.MockRouter, port: Port, server_version: JiraVersion
) -> None:
    create = respx_mock.post("/rest/api/2/issue").mock(
        return_value=_answer(server_version, "created")
    )
    comment = respx_mock.post("/rest/api/2/issue/ACME-1/comment").mock(
        return_value=_answer(server_version, "comment")
    )
    link = respx_mock.post("/rest/api/2/issueLink").mock(return_value=httpx.Response(201))
    assign = respx_mock.put("/rest/api/2/issue/ACME-1/assignee").mock(
        return_value=httpx.Response(204)
    )
    move = respx_mock.post("/rest/api/2/issue/ACME-1/transitions").mock(
        return_value=httpx.Response(204)
    )
    jira = port()

    created = jira.create({"fields": {"summary": "x"}})
    assert (created.key, created.id, created.api_url) == (
        "ACME-3",
        "10003",
        f"{BASE}/rest/api/2/issue/10003",
    )
    assert jira.comment("ACME-1", "Done.").id == "30002"
    jira.link("ACME-1", "Blocks", "ACME-2")
    jira.assign("ACME-1", None)
    jira.transition("ACME-1", "31", comment="Shipped", resolution="Done")

    def body(route: respx.Route) -> object:
        return json.loads(route.calls.last.request.content)

    assert body(create) == {"fields": {"summary": "x"}}
    assert body(comment) == {"body": "Done."}
    assert body(link) == {
        "type": {"name": "Blocks"},
        "inwardIssue": {"key": "ACME-1"},
        "outwardIssue": {"key": "ACME-2"},
    }
    assert body(assign) == {"name": None}
    assert body(move) == {
        "transition": {"id": "31"},
        "fields": {"resolution": {"name": "Done"}},
        "update": {"comment": [{"add": {"body": "Shipped"}}]},
    }


# ---- how it talks ---------------------------------------------------------------------


def test_it_sends_the_token_as_a_bearer(port: Port) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "jdoe"})
        )
        assert port().me().name == "jdoe"

    assert route.calls[0].request.headers["authorization"] == "Bearer jira_pat"
    assert route.calls[0].request.headers["accept"] == "application/json"


def test_a_context_path_in_the_server_url_prefixes_every_path(port: Port) -> None:
    """What ``jira.api_prefix`` did in 11.x: a Jira served under ``/jira``."""
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/jira/rest/api/2/myself").mock(
            return_value=httpx.Response(200, json={"name": "jdoe"})
        )
        assert port(server=JiraServer(url=f"{BASE}/jira/")).me().name == "jdoe"

    assert route.call_count == 1


def test_it_needs_a_server(port: Port) -> None:
    with pytest.raises(ConfigError, match=r"jira is not set up"):
        port(server=None).me()


@pytest.mark.parametrize("token", ["", "   "])
def test_a_blank_token_is_not_a_token(
    install: Callable[..., Install], monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setenv("JIRA_API_TOKEN", token)
    with install(Jira).running():
        assert TOKEN.find(JiraServer(url=BASE)) is None


def test_the_token_is_read_from_jira_api_token(
    install: Callable[..., Install], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIRA_API_TOKEN", "from-env")
    with install(Jira).running():
        assert TOKEN.get(JiraServer(url=BASE)).get_secret_value() == "from-env"


def test_settings_reject_a_blank_assigned_jql() -> None:
    with pytest.raises(ValueError, match="blank"):
        JiraSettings(assigned_jql=" ")


def test_search_posts_jql_and_honours_limit(port: Port) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/search").mock(return_value=_search_page(0, 1, "ABC-1"))
        rows = list(port().search("project = ABC", limit=1))

    assert [row.key for row in rows] == ["ABC-1"]
    request_json = json.loads(route.calls[0].request.content)
    assert (request_json["jql"], request_json["maxResults"]) == ("project = ABC", 1)


def test_search_walks_start_at_pages_until_total(port: Port) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/rest/api/2/search").mock(
            side_effect=[_search_page(0, 2, "ABC-1"), _search_page(1, 2, "ABC-2")]
        )
        rows = list(port(page_size=1).search("project = ABC", limit=None))

    assert [row.key for row in rows] == ["ABC-1", "ABC-2"]
    assert json.loads(route.calls[1].request.content)["startAt"] == 1


@pytest.mark.usefixtures("clock")  # the back-off waits on it
def test_search_retries_a_429_although_it_is_a_post(port: Port) -> None:
    """JQL search opts its idempotent POST into retry; mutating POSTs never retry."""
    with respx.mock(base_url=BASE) as mock:
        search = mock.post("/rest/api/2/search").mock(
            side_effect=[httpx.Response(429), _search_page(0, 1, "ABC-1")]
        )
        create = mock.post("/rest/api/2/issue").mock(return_value=httpx.Response(429))
        jira = port()
        rows = list(jira.search("project = ABC", limit=None))
        with pytest.raises(JiraError):
            jira.create({"fields": {}})

    assert [row.key for row in rows] == ["ABC-1"]
    assert (search.call_count, create.call_count) == (2, 1)


@pytest.mark.parametrize(
    ("path", "send"),
    [
        ("/rest/api/2/issue/ABC-1", lambda jira: jira.edit("ABC-1", {"update": {}})),
        ("/rest/api/2/issue/ABC-1/assignee", lambda jira: jira.assign("ABC-1", "ada")),
    ],
)
@pytest.mark.parametrize("answer", [429, 503])
@pytest.mark.usefixtures("clock")  # the back-off waits on it
def test_patch_puts_are_sent_once(
    path: str, send: Callable[[JiraApi], object], answer: int, port: Port
) -> None:
    """A patch's PUT can carry an ``update`` add, so a retry could apply it twice."""
    with respx.mock(base_url=BASE) as mock:
        put = mock.put(path).mock(return_value=httpx.Response(answer))
        with pytest.raises(JiraError):
            send(port())

    assert put.call_count == 1


@pytest.mark.usefixtures("clock")  # the back-off waits on it
def test_a_patch_put_is_not_resent_after_a_dropped_connection(port: Port) -> None:
    """Jira may have applied a PUT whose answer never arrived; only a failed connect is retried."""
    with respx.mock(base_url=BASE) as mock:
        dropped = mock.put("/rest/api/2/issue/ABC-1").mock(side_effect=httpx.ReadTimeout)
        unreached = mock.put("/rest/api/2/issue/ABC-1/assignee").mock(
            side_effect=[httpx.ConnectError("refused"), httpx.Response(204)]
        )
        jira = port()
        with pytest.raises(HttpTransportError):
            jira.edit("ABC-1", {"update": {}})
        jira.assign("ABC-1", "ada")

    assert (dropped.call_count, unreached.call_count) == (1, 2)


@pytest.mark.parametrize(
    ("path", "list_rows"),
    [
        (
            "/rest/agile/1.0/board",
            lambda jira: jira.boards(project=None, name=None, board_type=None, limit=None),
        ),
        ("/rest/agile/1.0/board/7/sprint", lambda jira: jira.sprints(7, state=None, limit=None)),
    ],
)
def test_agile_lists_continue_while_is_last_is_false_on_a_short_page(
    path: str, list_rows: Callable[[JiraApi], Iterator[Any]], port: Port
) -> None:
    def page(start: int, is_last: bool, *ids: int) -> httpx.Response:
        values = [{"id": value} for value in ids]
        body = {"startAt": start, "maxResults": 3, "isLast": is_last, "values": values}
        return httpx.Response(200, json=body)

    with respx.mock(base_url=BASE) as mock:
        route = mock.get(path).mock(side_effect=[page(0, False, 1, 2), page(2, True, 3)])
        rows = list(list_rows(port(page_size=3)))

    assert [row.id for row in rows] == [1, 2, 3]
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
def test_jiras_error_messages_reach_the_error(body: object, expected: str, port: Port) -> None:
    content = json.dumps(body) if isinstance(body, dict) else str(body)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/rest/api/2/issue").mock(return_value=httpx.Response(400, content=content))
        with pytest.raises(JiraError) as caught:
            port().create({"fields": {}})

    assert expected in str(caught.value)
    assert not isinstance(caught.value, HttpStatusError)


def test_errors_raised_while_paging_are_jiras(port: Port) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/agile/1.0/board").mock(return_value=httpx.Response(403))
        with pytest.raises(JiraError, match="permission denied"):
            list(port().boards(project=None, name=None, board_type=None, limit=None))


def test_path_values_are_encoded_as_one_segment(port: Port) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.route().mock(return_value=httpx.Response(200, json={"key": "X"}))
        jira = port()
        jira.project("A/B?x=y")
        jira.issue("..")

    assert [call.request.url.raw_path for call in route.calls] == [
        b"/rest/api/2/project/A%2FB%3Fx%3Dy",
        b"/rest/api/2/issue/%2E%2E?fields="
        + b"%2C".join(field.encode() for field in ISSUE_DETAIL_FIELDS),
    ]


# ---- not found, or not this API -------------------------------------------------------


def test_jiras_own_404_stays_not_found(jira: Invoke) -> None:
    gone = {"errorMessages": ["Issue Does Not Exist"], "errors": {}}
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ACME-9").mock(return_value=httpx.Response(404, json=gone))
        result = jira(["issues", "get", "ACME-9"])

    assert result.exit_code == 1, result.output
    assert "error: issue not found: 'ACME-9'" in result.stderr.splitlines()


def test_a_404_without_jiras_error_is_a_mismatch(port: Port, server_version: JiraVersion) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get("/rest/api/2/issue/ACME-1").mock(return_value=httpx.Response(404, text="<html/>"))
        with pytest.raises(ApiMismatch) as caught:
            port().issue("ACME-1")

    assert str(caught.value).startswith(
        f"{server_version} answered GET /rest/api/2/issue/ACME-1 in a way untaped-jira does not "
        "expect (HTTP 404)"
    )
    assert caught.value.hint == (
        f"jira.server_version says the server runs {server_version}; unset it to detect it"
    )
