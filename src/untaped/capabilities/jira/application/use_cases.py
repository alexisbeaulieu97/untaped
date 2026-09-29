"""Application use cases for Jira issue workflow."""

from __future__ import annotations

from collections.abc import Mapping
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
    change_line,
    payload_changes,
    referenced_fields,
    transition_changes,
)
from untaped.capabilities.jira.errors import JiraError, JiraTransitionError
from untaped.capability_api import UntapedError, UsageError, attribution, not_found, q


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
    """Update fields of one issue from a Jira-shaped payload, then its assignee.

    The assignee goes through the dedicated assignee endpoint, which works
    even when the assignee field is not on the issue's edit screen. A
    ``None`` payload or assignee skips that request.
    """

    def __init__(self, client: JiraIssueWriter, *, base_url: str | None = None) -> None:
        self._client = client
        self._base_url = base_url

    def __call__(
        self,
        issue_key: str,
        payload: dict[str, Any] | None,
        *,
        assignee: dict[str, Any] | None = None,
    ) -> IssueOutcome:
        if payload is not None:
            self._client.edit_issue(issue_key, payload)
        if assignee is not None:
            try:
                self._client.assign_issue(issue_key, assignee)
            except JiraError as err:
                if payload is None:
                    raise
                raise JiraError(
                    f"fields updated, but assigning failed: {err}",
                    hint=err.hint,
                    **attribution(err),
                ) from err
        return IssueOutcome(
            action="updated", key=issue_key, url=browse_url(self._base_url, issue_key)
        )


class PreviewPatch:
    """Describe an issue edit and assignment against the issue's current values.

    Reads only the fields the edit touches (plus ``assignee`` when assigning)
    in one request; returns the edit's lines and the assignment's lines.
    """

    def __init__(self, client: JiraIssueReader) -> None:
        self._client = client

    def __call__(
        self,
        issue_key: str,
        payload: dict[str, Any] | None,
        *,
        assignee: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[str]]:
        names = referenced_fields(payload) + (["assignee"] if assignee is not None else [])
        current = (
            (self._client.get_issue(issue_key, fields=names).get("fields") or {}) if names else {}
        )
        edit = payload_changes(payload, current) if payload is not None else []
        assign = (
            [change_line("assignee", assignee, old=current.get("assignee"))]
            if assignee is not None
            else []
        )
        return edit, assign


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
    ) -> dict[str, Any]:
        """The transition to apply to ``issue_key``: ``{"id": ...}`` for an id, else looked up.

        A transition found by name keeps Jira's ``name`` and ``to`` (its
        target status), so a preview need not list the transitions again.
        """
        self.check_selector(transition_id, transition_name)
        if transition_id:
            return {"id": transition_id}
        return self._resolve_transition_name(issue_key, transition_name or "")

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

    def _resolve_transition_name(self, issue_key: str, name: str) -> dict[str, Any]:
        transitions = self._client.list_transitions(issue_key)
        matches = [t for t in transitions if str(t.get("name", "")).casefold() == name.casefold()]
        if not matches:
            known = sorted({str(t.get("name", "")) for t in transitions})
            raise JiraTransitionError(
                f"{not_found('transition', name, known=known)} (issue {issue_key})",
                category="not_found",
            )
        if len(matches) > 1:
            raise JiraTransitionError(
                f"multiple transitions named {q(name)} are available for {issue_key}"
            )
        return {**matches[0], "id": str(matches[0]["id"])}


class PreviewTransition:
    """Describe a transition: its name, the status change, resolution and comment.

    One read per issue: its status and resolution, plus (when ``transition``
    came from an id, without its target) the transitions it offers. A failed
    read shows ``(unknown)`` rather than failing the batch.
    """

    def __init__(self, client: JiraIssueReader) -> None:
        self._client = client

    def __call__(
        self,
        issue_key: str,
        transition: Mapping[str, Any],
        *,
        comment: str | None = None,
        resolution: str | None = None,
    ) -> list[str]:
        known = "to" in transition
        try:
            issue = self._client.get_issue(
                issue_key,
                fields=["status", "resolution"],
                expand=None if known else "transitions",
            )
        except UntapedError:
            return transition_changes(transition, None, comment=comment, resolution=resolution)
        match, available = transition, True
        if not known:
            offered = {str(t.get("id")): t for t in issue.get("transitions") or []}
            found = offered.get(str(transition["id"]))
            match, available = found or transition, found is not None
        return transition_changes(
            match,
            issue.get("fields") or {},
            available=available,
            comment=comment,
            resolution=resolution,
        )


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
