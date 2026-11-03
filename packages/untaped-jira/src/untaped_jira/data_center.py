"""The Data Center adapter: jira's port over REST API 2 and Agile 1.0.

It owns this API's paths, request bodies, paging and retries, and reads
Jira's answers into jira's records through :mod:`untaped_jira.parse`. Every
call runs inside :func:`~untaped_jira.errors.map_jira_errors` (Jira's error
statuses become jira's errors) and ``ApiMismatch.watch`` (a gone path or an
answer that does not read is a mismatch; Jira's own 404, which carries
``errorMessages``, stays "not found").
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Literal
from urllib.parse import quote

from untaped.sdk.network import ApiAdapter, ApiMismatch, HttpClient, RetryPolicy, paginate_offset
from untaped_jira.errors import map_jira_errors
from untaped_jira.parse import (
    ISSUE_DETAIL_FIELDS,
    ISSUE_ROW_FIELDS,
    parse_board,
    parse_comment,
    parse_issue,
    parse_project,
    parse_sprint,
    parse_transition,
    parse_user,
)
from untaped_jira.ports import Created, Current, JiraApi
from untaped_jira.records import Board, Comment, Issue, Project, Sprint, Transition, User
from untaped_jira.server import JiraVersion, is_jira_error
from untaped_jira.settings import JiraSettings

API = "/rest/api/2"
AGILE = "/rest/agile/1.0"
_JSON = {"Accept": "application/json", "Content-Type": "application/json"}

# Jira's JQL search is a POST to an idempotent ``/search`` endpoint. Opt just
# that call into retry by widening the retryable methods to include POST: the
# mutating POSTs (create, comment, transition, link) don't go through
# ``paginate_offset``, so the client's default policy sends them once (it
# retries only idempotent methods).
_SEARCH_RETRY = RetryPolicy(
    idempotent_methods=frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "POST"})
)
# A patch's PUTs are not always idempotent: an ``update`` add (a comment, a
# label) applies again on every attempt. Treat no method as idempotent so
# they're sent once like the POSTs, retrying only a connect that never reached
# Jira.
_SEND_ONCE = RetryPolicy(idempotent_methods=frozenset())


def _segment(value: str) -> str:
    """``value`` percent-encoded as exactly one URL path segment (dots too, so no ``..``)."""
    return quote(value, safe="").replace(".", "%2E")


@contextmanager
def _talking(*, noun: str | None = None, name: str | None = None) -> Iterator[None]:
    """Jira's errors as jira's, and an answer this API never gives as a mismatch."""
    with map_jira_errors(noun=noun, name=name), ApiMismatch.watch(vendor_error=is_jira_error):
        yield


class DataCenter(JiraApi, ApiAdapter[JiraSettings, JiraVersion]):
    """Jira Data Center 8 and later.

    REST API 2 and Agile 1.0, with a personal access token.
    """

    recorded = (
        JiraVersion.parse("Jira Data Center 9.12.15"),
        JiraVersion.parse("Jira Data Center 11.2.1"),
    )

    @classmethod
    def serves(cls, version: JiraVersion) -> bool:
        return version.release is not None and version.release >= (8,)  # Cloud has none

    @property
    def _http(self) -> HttpClient:
        return self.connect(headers=_JSON)

    # ---- people and issues ----------------------------------------------------

    def me(self) -> User:
        with _talking():
            return parse_user(self._http.get_json_dict(f"{API}/myself"))

    def issue(self, key: str) -> Issue:
        with _talking(noun="issue", name=key):
            return parse_issue(self._get_issue(key, ISSUE_DETAIL_FIELDS), detail=True)

    def current(self, key: str, fields: Sequence[str], *, transitions: bool = False) -> Current:
        with _talking(noun="issue", name=key):
            raw = self._get_issue(key, fields, expand="transitions" if transitions else None)
            return Current(
                fields=dict(raw.get("fields") or {}),
                transitions=[parse_transition(each) for each in raw.get("transitions") or []],
            )

    def _get_issue(
        self, key: str, fields: Sequence[str], *, expand: str | None = None
    ) -> dict[str, Any]:
        params = {"fields": ",".join(fields)}
        if expand:
            params["expand"] = expand
        return self._http.get_json_dict(f"{API}/issue/{_segment(key)}", params=params)

    def search(self, jql: str, *, limit: int | None) -> Iterator[Issue]:
        with _talking():
            for raw in self._pages(
                "POST",
                f"{API}/search",
                "issues",
                limit=limit,
                body={"jql": jql, "fields": list(ISSUE_ROW_FIELDS)},
                retry=_SEARCH_RETRY,
            ):
                yield parse_issue(raw)

    def comments(self, key: str, *, limit: int | None = None) -> Iterator[Comment]:
        with _talking(noun="issue", name=key):
            path = f"{API}/issue/{_segment(key)}/comment"
            for raw in self._pages("GET", path, "comments", limit=limit):
                yield parse_comment(raw, issue_key=key)

    # ---- writes ---------------------------------------------------------------

    def create(self, document: Mapping[str, Any]) -> Created:
        with _talking():
            created = self._http.post_json(f"{API}/issue", json=dict(document))
            return Created(
                key=_text(created.get("key")),
                id=_text(created.get("id")),
                api_url=_text(created.get("self")),
            )

    def edit(self, key: str, document: Mapping[str, Any]) -> None:
        with _talking(noun="issue", name=key):
            self._http.request_json(
                "PUT", f"{API}/issue/{_segment(key)}", json=dict(document), retry=_SEND_ONCE
            )

    def assign(self, key: str, username: str | None) -> None:
        with _talking(noun="issue", name=key):
            self._http.request_json(
                "PUT",
                f"{API}/issue/{_segment(key)}/assignee",
                json={"name": username},
                retry=_SEND_ONCE,
            )

    def comment(self, key: str, body: str) -> Comment:
        with _talking(noun="issue", name=key):
            raw = self._http.post_json(f"{API}/issue/{_segment(key)}/comment", json={"body": body})
            return parse_comment(raw, issue_key=key)

    def link(self, key: str, link_type: str, other: str) -> None:
        # Jira names the issue that shows the outward phrase (``blocks``) the
        # ``inwardIssue``, so ``OPS-1 Blocks OPS-2`` makes OPS-1 block OPS-2.
        body = {
            "type": {"name": link_type},
            "inwardIssue": {"key": key},
            "outwardIssue": {"key": other},
        }
        with _talking():
            self._http.request_json("POST", f"{API}/issueLink", json=body)

    def transitions(self, key: str) -> list[Transition]:
        with _talking(noun="issue", name=key):
            raw = self._http.get_json_dict(f"{API}/issue/{_segment(key)}/transitions")
            return [parse_transition(each) for each in raw.get("transitions") or []]

    def transition(
        self, key: str, transition_id: str, *, comment: str | None, resolution: str | None
    ) -> None:
        body: dict[str, Any] = {"transition": {"id": transition_id}}
        if resolution is not None:
            body["fields"] = {"resolution": {"name": resolution}}
        if comment is not None:
            body["update"] = {"comment": [{"add": {"body": comment}}]}
        with _talking(noun="issue", name=key):
            self._http.request_json("POST", f"{API}/issue/{_segment(key)}/transitions", json=body)

    # ---- projects, boards and sprints -----------------------------------------

    def projects(self) -> Iterator[Project]:
        with _talking():
            for raw in self._http.get_json_list(f"{API}/project"):
                yield parse_project(raw)

    def project(self, key: str) -> Project:
        with _talking(noun="project", name=key):
            return parse_project(self._http.get_json_dict(f"{API}/project/{_segment(key)}"))

    def boards(
        self,
        *,
        project: str | None,
        name: str | None,
        board_type: str | None,
        limit: int | None,
    ) -> Iterator[Board]:
        given = {"projectKeyOrId": project, "name": name, "type": board_type}
        params = {param: value for param, value in given.items() if value}
        with _talking():
            for raw in self._pages(
                "GET", f"{AGILE}/board", "values", limit=limit, params=params, last_flag="isLast"
            ):
                yield parse_board(raw)

    def sprints(self, board_id: int, *, state: str | None, limit: int | None) -> Iterator[Sprint]:
        params = {"state": state} if state else None
        with _talking(noun="board", name=str(board_id)):
            for raw in self._pages(
                "GET",
                f"{AGILE}/board/{board_id}/sprint",
                "values",
                limit=limit,
                params=params,
                last_flag="isLast",
            ):
                yield parse_sprint(raw)

    def _pages(
        self,
        method: Literal["GET", "POST"],
        path: str,
        item_key: str,
        *,
        limit: int | None,
        **options: Any,
    ) -> Iterator[dict[str, Any]]:
        """Rows under ``item_key`` across Jira's ``startAt``/``maxResults`` pages."""
        return paginate_offset(
            self._http,
            method,
            path,
            item_key=item_key,
            page_size=self.settings.page_size,
            limit=limit,
            start_param="startAt",
            size_param="maxResults",
            **options,
        )


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = ["DataCenter"]
