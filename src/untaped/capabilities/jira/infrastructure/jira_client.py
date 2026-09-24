"""HTTP client for Jira Data Center REST APIs."""

from __future__ import annotations

from collections.abc import Iterator
from types import TracebackType
from typing import Any

from untaped.capabilities.jira.domain.models import ISSUE_DETAIL_FIELDS, ISSUE_ROW_FIELDS
from untaped.capabilities.jira.infrastructure.errors import map_jira_errors
from untaped.capabilities.jira.settings import JiraSettings
from untaped.capability_api import HttpSettings, RetryPolicy, connected_client, paginate_offset

# Jira's JQL search is a POST to an idempotent ``/search`` endpoint. Opt just
# that call into retry by widening the retryable methods to include POST — the
# tool's mutating POSTs (create/comment/transition) don't go through
# ``paginate_offset``, so they keep the client's default (non-retrying) policy.
_SEARCH_RETRY = RetryPolicy(
    idempotent_methods=frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "POST"})
)


class JiraClient:
    """Talks to Jira Data Center using a configured personal access token."""

    def __init__(self, config: JiraSettings, *, http: HttpSettings | None = None) -> None:
        self._http = connected_client(
            config,
            section="jira",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            http=http,
        )
        self._api_prefix = config.api_prefix
        self._agile_prefix = config.agile_prefix
        self._page_size = config.page_size

    def _api(self, path: str) -> str:
        return f"{self._api_prefix}/{path.lstrip('/')}"

    def _agile(self, path: str) -> str:
        return f"{self._agile_prefix}/{path.lstrip('/')}"

    def me(self) -> dict[str, Any]:
        with map_jira_errors():
            return self._http.get_json_dict(self._api("myself"))

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        with map_jira_errors(noun="issue", name=issue_key):
            return self._http.get_json_dict(
                self._api(f"issue/{issue_key}"),
                params={"fields": ",".join(ISSUE_DETAIL_FIELDS)},
            )

    def search_issues(self, jql: str, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
        with map_jira_errors():
            yield from paginate_offset(
                self._http,
                "POST",
                self._api("search"),
                item_key="issues",
                body={"jql": jql, "fields": list(ISSUE_ROW_FIELDS)},
                page_size=self._page_size,
                limit=limit,
                start_param="startAt",
                size_param="maxResults",
                retry=_SEARCH_RETRY,
            )

    def list_comments(
        self, issue_key: str, *, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        with map_jira_errors(noun="issue", name=issue_key):
            yield from paginate_offset(
                self._http,
                "GET",
                self._api(f"issue/{issue_key}/comment"),
                item_key="comments",
                page_size=self._page_size,
                limit=limit,
                start_param="startAt",
                size_param="maxResults",
            )

    def create_issue(self, payload: dict[str, Any]) -> dict[str, Any]:
        with map_jira_errors():
            return self._http.post_json(self._api("issue"), json=payload)  # type: ignore[no-any-return]

    def edit_issue(self, issue_key: str, payload: dict[str, Any]) -> None:
        with map_jira_errors(noun="issue", name=issue_key):
            self._http.request_json("PUT", self._api(f"issue/{issue_key}"), json=payload)

    def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        with map_jira_errors(noun="issue", name=issue_key):
            return self._http.post_json(  # type: ignore[no-any-return]
                self._api(f"issue/{issue_key}/comment"),
                json={"body": body},
            )

    def create_link(self, payload: dict[str, Any]) -> None:
        with map_jira_errors():
            self._http.request_json("POST", self._api("issueLink"), json=payload)

    def list_transitions(self, issue_key: str) -> list[dict[str, Any]]:
        with map_jira_errors(noun="issue", name=issue_key):
            payload = self._http.get_json_dict(self._api(f"issue/{issue_key}/transitions"))
        return list(payload.get("transitions") or [])

    def transition_issue(self, issue_key: str, payload: dict[str, Any]) -> None:
        with map_jira_errors(noun="issue", name=issue_key):
            self._http.request_json(
                "POST", self._api(f"issue/{issue_key}/transitions"), json=payload
            )

    def list_projects(self) -> Iterator[dict[str, Any]]:
        with map_jira_errors():
            yield from self._http.get_json_list(self._api("project"))

    def get_project(self, project_key: str) -> dict[str, Any]:
        with map_jira_errors(noun="project", name=project_key):
            return self._http.get_json_dict(self._api(f"project/{project_key}"))

    def list_boards(
        self,
        *,
        project_key_or_id: str | None = None,
        name: str | None = None,
        board_type: str | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {}
        if project_key_or_id:
            params["projectKeyOrId"] = project_key_or_id
        if name:
            params["name"] = name
        if board_type:
            params["type"] = board_type
        with map_jira_errors():
            yield from paginate_offset(
                self._http,
                "GET",
                self._agile("board"),
                item_key="values",
                params=params,
                page_size=self._page_size,
                limit=limit,
                start_param="startAt",
                size_param="maxResults",
                last_flag="isLast",
            )

    def list_sprints(
        self,
        board_id: int,
        *,
        state: str | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        params = {"state": state} if state else None
        with map_jira_errors(noun="board", name=str(board_id)):
            yield from paginate_offset(
                self._http,
                "GET",
                self._agile(f"board/{board_id}/sprint"),
                item_key="values",
                params=params,
                page_size=self._page_size,
                limit=limit,
                start_param="startAt",
                size_param="maxResults",
                last_flag="isLast",
            )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
