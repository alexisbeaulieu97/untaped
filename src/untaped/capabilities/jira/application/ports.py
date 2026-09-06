"""Application-layer protocols for the Jira tool."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol


class JiraMeService(Protocol):
    """Authenticated-user fetch contract."""

    def me(self) -> dict[str, Any]: ...


class JiraIssueReader(Protocol):
    """Issue read and search contract."""

    def get_issue(self, issue_key: str) -> dict[str, Any]: ...

    def search_issues(self, jql: str, *, limit: int | None = None) -> Iterator[dict[str, Any]]: ...


class JiraIssueWriter(Protocol):
    """Issue mutation contract."""

    def create_issue(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def edit_issue(self, issue_key: str, payload: dict[str, Any]) -> None: ...

    def add_comment(self, issue_key: str, body: str) -> dict[str, Any]: ...


class JiraTransitionService(Protocol):
    """Issue workflow transition contract."""

    def list_transitions(self, issue_key: str) -> list[dict[str, Any]]: ...

    def transition_issue(self, issue_key: str, transition_id: str) -> None: ...


class JiraLookupService(Protocol):
    """Project, board, and sprint lookup contract."""

    def list_projects(self) -> Iterator[dict[str, Any]]: ...

    def get_project(self, project_key: str) -> dict[str, Any]: ...

    def list_boards(
        self,
        *,
        project_key_or_id: str | None = None,
        name: str | None = None,
        board_type: str | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]: ...

    def list_sprints(
        self,
        board_id: int,
        *,
        state: str | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]: ...
