"""The records ``untaped jira`` writes.

A record's kind comes from the command that prints it (``Issue`` is
``jira.issue``). Turning a Jira REST payload into one of these is
:mod:`untaped_jira.parse`'s job; the models hold only what goes on the
wire, in the order it is written. ``Column`` marks a table's default
columns; ``Following`` fields print as their own table after it.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from untaped.sdk import Column, Following, Key, OutcomeRecord, Record, UtcTimestamp


class User(Record):
    """The authenticated Jira user (``whoami``)."""

    name: str | None = None
    key: str | None = None
    display_name: str | None = None
    email_address: str | None = None


class IssueLink(Record):
    """One link on an issue, read from the issue's own ``issuelinks`` field.

    ``direction`` says which side of the link this issue is on: ``outward``
    when this issue is the link's source (Jira shows the type's outward
    phrase, e.g. ``blocks``), ``inward`` when it is the target (the inward
    phrase, e.g. ``is blocked by``). ``relation`` is that phrase verbatim.
    """

    key: Annotated[str, Column]
    summary: Annotated[str, Column] = ""
    status: Annotated[str, Column] = ""
    type: str = ""
    direction: str = ""
    relation: Annotated[str, Column] = ""
    url: str = ""


class Comment(Record):
    """One issue comment."""

    id: str
    issue_key: str = ""
    author: Annotated[str, Column] = ""
    created_at: Annotated[UtcTimestamp | None, Column] = None
    updated_at: UtcTimestamp | None = None
    body: Annotated[str, Column] = ""
    api_url: str | None = None


class Issue(Record):
    """One issue, with Jira's nested fields flattened.

    Each command's page says which fields it fills; a field a command does
    not fill is ``null``.
    """

    key: Annotated[str, Key]
    summary: str = ""
    status: str = ""
    assignee: str = ""
    updated_at: UtcTimestamp | None = None
    url: str = Field(default="", description="The issue's page, to open in a browser.")
    api_url: str | None = Field(default=None, description="The issue's REST API address.")
    issue_type: str = ""
    priority: str = ""
    reporter: str | None = None
    labels: list[str] | None = None
    created_at: UtcTimestamp | None = None
    resolution: str | None = None
    description: str | None = None
    links: Annotated[list[IssueLink] | None, Following] = None
    comments: Annotated[list[Comment] | None, Following] = None


class IssueOutcome(OutcomeRecord):
    """The result of one issue mutation.

    ``action`` is ``created``, ``updated``, ``commented``, ``transitioned``,
    ``linked``, or ``planned`` under ``--dry-run`` (where a new issue has no ``key`` yet).
    """

    key: Annotated[str | None, Key] = None
    id: str | None = None
    url: str | None = None
    api_url: str | None = None
    transition_id: str | None = None
    comment_id: str | None = None
    link_type: str | None = None
    linked_key: str | None = None


class Transition(Record):
    """One workflow transition available on an issue.

    ``to_status`` is the status it leads to (``None`` when Jira does not say).
    """

    id: Annotated[str, Column]
    name: Annotated[str, Column]
    to_status: Annotated[str | None, Column] = None


class Project(Record):
    """One Jira project."""

    key: Annotated[str, Key, Column]
    name: Annotated[str, Column] = ""
    id: str = ""
    project_type_key: Annotated[str | None, Column] = None


class Board(Record):
    """One Jira Software board."""

    id: Annotated[int, Column]
    name: Annotated[str, Column] = ""
    type: Annotated[str, Column] = ""
    api_url: str | None = None


class Sprint(Record):
    """One Jira Software sprint."""

    id: Annotated[int, Column]
    name: Annotated[str, Column] = ""
    state: Annotated[str, Column] = ""
    start_at: Annotated[UtcTimestamp | None, Column] = None
    end_at: Annotated[UtcTimestamp | None, Column] = None
    goal: Annotated[str | None, Column] = None
    origin_board_id: int | None = None
