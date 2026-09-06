"""Application use cases for Jira ticket workflow."""

from __future__ import annotations

from typing import Any

from untaped.api import ConfigError
from untaped.capabilities.jira.application.ports import (
    JiraIssueReader,
    JiraIssueWriter,
    JiraLookupService,
    JiraMeService,
    JiraTransitionService,
)
from untaped.capabilities.jira.domain import (
    BoardResult,
    CommentResult,
    IssueMutationResult,
    IssueResult,
    JiraIssueSearchFilters,
    JiraUser,
    ProjectResult,
    SprintResult,
    TransitionResult,
)


class WhoAmI:
    """Fetch the authenticated Jira user."""

    def __init__(self, client: JiraMeService) -> None:
        self._client = client

    def __call__(self) -> JiraUser:
        return JiraUser.model_validate(self._client.me())


class GetIssue:
    """Fetch one issue by key or id."""

    def __init__(self, client: JiraIssueReader) -> None:
        self._client = client

    def __call__(self, issue_key: str) -> IssueResult:
        return IssueResult.model_validate(self._client.get_issue(issue_key))


class SearchIssues:
    """Search issues using rendered JQL."""

    def __init__(self, client: JiraIssueReader) -> None:
        self._client = client

    def __call__(self, filters: JiraIssueSearchFilters, *, limit: int | None) -> list[IssueResult]:
        return [
            IssueResult.model_validate(issue)
            for issue in self._client.search_issues(filters.to_jql(), limit=limit)
        ]


class CreateIssue:
    """Create one issue from a Jira-shaped payload."""

    def __init__(self, client: JiraIssueWriter) -> None:
        self._client = client

    def __call__(self, payload: dict[str, Any]) -> IssueMutationResult:
        return IssueMutationResult.model_validate(self._client.create_issue(payload))


class EditIssue:
    """Edit one issue from a Jira-shaped payload."""

    def __init__(self, client: JiraIssueWriter) -> None:
        self._client = client

    def __call__(self, issue_key: str, payload: dict[str, Any]) -> IssueMutationResult:
        self._client.edit_issue(issue_key, payload)
        return IssueMutationResult(key=issue_key, status="updated")


class AddComment:
    """Add one comment to an issue."""

    def __init__(self, client: JiraIssueWriter) -> None:
        self._client = client

    def __call__(self, issue_key: str, body: str) -> CommentResult:
        result = self._client.add_comment(issue_key, body)
        return CommentResult.model_validate({**result, "issue": issue_key})


class ListTransitions:
    """List available workflow transitions for one issue."""

    def __init__(self, client: JiraTransitionService) -> None:
        self._client = client

    def __call__(self, issue_key: str) -> list[TransitionResult]:
        return [
            TransitionResult.model_validate(transition)
            for transition in self._client.list_transitions(issue_key)
        ]


class TransitionIssue:
    """Apply a workflow transition by id or unambiguous name."""

    def __init__(self, client: JiraTransitionService) -> None:
        self._client = client

    def __call__(
        self,
        issue_key: str,
        *,
        transition_id: str | None = None,
        transition_name: str | None = None,
    ) -> IssueMutationResult:
        if bool(transition_id) == bool(transition_name):
            raise ConfigError("provide exactly one of --id or --to")
        resolved = transition_id or self._resolve_transition_name(issue_key, transition_name or "")
        self._client.transition_issue(issue_key, resolved)
        return IssueMutationResult(key=issue_key, status="transitioned", transition_id=resolved)

    def _resolve_transition_name(self, issue_key: str, name: str) -> str:
        matches = [
            t
            for t in self._client.list_transitions(issue_key)
            if str(t.get("name", "")).casefold() == name.casefold()
        ]
        if not matches:
            raise ConfigError(f"no transition named {name!r} is available for {issue_key}")
        if len(matches) > 1:
            raise ConfigError(f"multiple transitions named {name!r} are available for {issue_key}")
        return str(matches[0]["id"])


class ListProjects:
    """List visible Jira projects."""

    def __init__(self, client: JiraLookupService) -> None:
        self._client = client

    def __call__(self) -> list[ProjectResult]:
        return [ProjectResult.model_validate(project) for project in self._client.list_projects()]


class GetProject:
    """Fetch one Jira project."""

    def __init__(self, client: JiraLookupService) -> None:
        self._client = client

    def __call__(self, project_key: str) -> ProjectResult:
        return ProjectResult.model_validate(self._client.get_project(project_key))


class ListBoards:
    """List visible Jira Software boards."""

    def __init__(self, client: JiraLookupService) -> None:
        self._client = client

    def __call__(
        self,
        *,
        project_key_or_id: str | None,
        name: str | None,
        board_type: str | None,
        limit: int | None,
    ) -> list[BoardResult]:
        return [
            BoardResult.model_validate(board)
            for board in self._client.list_boards(
                project_key_or_id=project_key_or_id,
                name=name,
                board_type=board_type,
                limit=limit,
            )
        ]


class ListSprints:
    """List Jira Software sprints for a board."""

    def __init__(self, client: JiraLookupService, *, default_board_id: int | None = None) -> None:
        self._client = client
        self._default_board_id = default_board_id

    def __call__(
        self,
        *,
        board_id: int | None,
        state: str | None,
        limit: int | None,
    ) -> list[SprintResult]:
        resolved_board_id = board_id or self._default_board_id
        if resolved_board_id is None:
            raise ConfigError(
                "board id is required (pass --board-id or configure jira.default_board_id)"
            )
        return [
            SprintResult.model_validate(sprint)
            for sprint in self._client.list_sprints(resolved_board_id, state=state, limit=limit)
        ]
