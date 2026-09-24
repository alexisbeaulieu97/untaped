"""Application use cases for Jira issue workflow."""

from __future__ import annotations

from typing import Any

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
    IssueDetailResult,
    IssueOutcome,
    IssueResult,
    JiraIssueSearchFilters,
    JiraUser,
    ProjectResult,
    SprintResult,
    TransitionResult,
    browse_url,
    build_link_payload,
    build_transition_payload,
)
from untaped.capabilities.jira.errors import JiraTransitionError
from untaped.capability_api import UsageError, not_found, q


class WhoAmI:
    """Fetch the authenticated Jira user."""

    def __init__(self, client: JiraMeService) -> None:
        self._client = client

    def __call__(self) -> JiraUser:
        return JiraUser.model_validate(self._client.me())


class GetIssue:
    """Fetch one issue by key or id, optionally with all of its comments."""

    def __init__(self, client: JiraIssueReader, *, comments: bool = False) -> None:
        self._client = client
        self._comments = comments

    def __call__(self, issue_key: str) -> IssueDetailResult:
        issue = IssueDetailResult.model_validate(self._client.get_issue(issue_key))
        if not self._comments:
            return issue
        return issue.model_copy(update={"comments": ListComments(self._client)(issue.key)})


class ListComments:
    """List the comments of one issue, oldest first."""

    def __init__(self, client: JiraIssueReader) -> None:
        self._client = client

    def __call__(self, issue_key: str, *, limit: int | None = None) -> list[CommentResult]:
        return [
            CommentResult.model_validate({**comment, "issue_key": issue_key})
            for comment in self._client.list_comments(issue_key, limit=limit)
        ]


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

    def __init__(self, client: JiraIssueWriter, *, base_url: str | None = None) -> None:
        self._client = client
        self._base_url = base_url

    def __call__(self, payload: dict[str, Any]) -> IssueOutcome:
        created = self._client.create_issue(payload)
        key = _optional_str(created.get("key"))
        return IssueOutcome(
            action="created",
            key=key,
            id=_optional_str(created.get("id")),
            url=browse_url(self._base_url, key) if key else None,
            api_url=_optional_str(created.get("self")),
        )


class PatchIssue:
    """Update fields of one issue from a Jira-shaped payload."""

    def __init__(self, client: JiraIssueWriter, *, base_url: str | None = None) -> None:
        self._client = client
        self._base_url = base_url

    def __call__(self, issue_key: str, payload: dict[str, Any]) -> IssueOutcome:
        self._client.edit_issue(issue_key, payload)
        return IssueOutcome(
            action="updated", key=issue_key, url=browse_url(self._base_url, issue_key)
        )


class AddComment:
    """Add one comment to an issue."""

    def __init__(self, client: JiraIssueWriter, *, base_url: str | None = None) -> None:
        self._client = client
        self._base_url = base_url

    def __call__(self, issue_key: str, body: str) -> IssueOutcome:
        result = self._client.add_comment(issue_key, body)
        return IssueOutcome(
            action="commented",
            key=issue_key,
            url=browse_url(self._base_url, issue_key),
            comment_id=_optional_str(result.get("id")),
        )


class LinkIssues:
    """Link two issues with a named link type."""

    def __init__(self, client: JiraIssueWriter, *, base_url: str | None = None) -> None:
        self._client = client
        self._base_url = base_url

    def __call__(self, issue_key: str, link_type: str, other_key: str) -> IssueOutcome:
        self._client.create_link(build_link_payload(issue_key, link_type, other_key))
        return IssueOutcome(
            action="linked",
            key=issue_key,
            url=browse_url(self._base_url, issue_key),
            link_type=link_type,
            linked_key=other_key,
        )


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

    def __init__(self, client: JiraTransitionService, *, base_url: str | None = None) -> None:
        self._client = client
        self._base_url = base_url

    @staticmethod
    def check_selector(transition_id: str | None, transition_name: str | None) -> None:
        """Reject a call that names neither or both of ``--id`` and ``--to``."""
        if bool(transition_id) == bool(transition_name):
            raise UsageError("provide exactly one of --id or --to")

    def resolve(
        self,
        issue_key: str,
        *,
        transition_id: str | None = None,
        transition_name: str | None = None,
    ) -> str:
        """The transition id to apply to ``issue_key`` (a name is looked up)."""
        self.check_selector(transition_id, transition_name)
        return transition_id or self._resolve_transition_name(issue_key, transition_name or "")

    def __call__(
        self,
        issue_key: str,
        transition_id: str,
        *,
        comment: str | None = None,
        resolution: str | None = None,
    ) -> IssueOutcome:
        payload = build_transition_payload(transition_id, comment=comment, resolution=resolution)
        self._client.transition_issue(issue_key, payload)
        return IssueOutcome(
            action="transitioned",
            key=issue_key,
            url=browse_url(self._base_url, issue_key),
            transition_id=transition_id,
        )

    def _resolve_transition_name(self, issue_key: str, name: str) -> str:
        transitions = self._client.list_transitions(issue_key)
        matches = [t for t in transitions if str(t.get("name", "")).casefold() == name.casefold()]
        if not matches:
            known = sorted({str(t.get("name", "")) for t in transitions})
            raise JiraTransitionError(
                f"{not_found('transition', name, known=known)} (issue {issue_key})"
            )
        if len(matches) > 1:
            raise JiraTransitionError(
                f"multiple transitions named {q(name)} are available for {issue_key}"
            )
        return str(matches[0]["id"])


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


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
            raise UsageError(
                "board id is required (pass --board-id or configure jira.default_board_id)"
            )
        return [
            SprintResult.model_validate(sprint)
            for sprint in self._client.list_sprints(resolved_board_id, state=state, limit=limit)
        ]
