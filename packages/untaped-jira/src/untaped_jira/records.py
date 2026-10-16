"""The records ``untaped jira`` writes, each declaring its kind on the model.

The app imports this module, so ``untaped plugin schema jira.issue`` finds
every jira kind before any command runs. Everything else names the model:
``accepts=(Issue, IssueOutcome)``, ``kind_of(Issue)``. Turning a Jira REST
payload into one of these is :mod:`untaped_jira.domain.parse`'s job; the
models hold only what goes on the wire, in the order it is written.
"""

from __future__ import annotations

from untaped.sdk import OutcomeRecord, Record, UtcTimestamp


class User(Record, kind="jira.user"):
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
    The linked issue carries only what the link embeds; it is not fetched.
    """

    key: str
    summary: str = ""
    status: str = ""
    type: str = ""
    direction: str = ""
    relation: str = ""
    url: str = ""


class Comment(Record, kind="jira.comment"):
    """One issue comment."""

    id: str
    issue_key: str = ""
    author: str = ""
    created_at: UtcTimestamp | None = None
    updated_at: UtcTimestamp | None = None
    body: str = ""
    api_url: str | None = None


class Issue(Record, kind="jira.issue"):
    """One issue, with Jira's nested fields flattened.

    ``issues search`` and ``issues assigned`` fill the row fields, up to
    ``priority``; ``issues get`` also fills the detail fields after it
    (``reporter`` to ``links``), and ``comments`` only with ``--comments``.
    A field a command does not fill is ``null``.
    """

    key: str
    summary: str = ""
    status: str = ""
    assignee: str = ""
    updated_at: UtcTimestamp | None = None
    url: str = ""
    api_url: str | None = None
    issue_type: str = ""
    priority: str = ""
    reporter: str | None = None
    labels: list[str] | None = None
    created_at: UtcTimestamp | None = None
    resolution: str | None = None
    description: str | None = None
    links: list[IssueLink] | None = None
    comments: list[Comment] | None = None


class IssueOutcome(OutcomeRecord, kind="jira.issue_outcome"):
    """The result of one issue mutation.

    ``action`` is ``created``, ``updated``, ``commented``, ``transitioned``,
    ``linked``, or ``planned`` under ``--dry-run`` (where a new issue has no ``key`` yet).
    """

    key: str | None = None
    id: str | None = None
    url: str | None = None
    api_url: str | None = None
    transition_id: str | None = None
    comment_id: str | None = None
    link_type: str | None = None
    linked_key: str | None = None


class Transition(Record, kind="jira.transition"):
    """One workflow transition available on an issue.

    ``to_status`` is the status it leads to (``None`` when Jira does not say).
    """

    id: str
    name: str
    to_status: str | None = None


class Project(Record, kind="jira.project"):
    """One Jira project."""

    key: str
    name: str = ""
    id: str = ""
    project_type_key: str | None = None


class Board(Record, kind="jira.board"):
    """One Jira Software board."""

    id: int
    name: str = ""
    type: str = ""
    api_url: str | None = None


class Sprint(Record, kind="jira.sprint"):
    """One Jira Software sprint."""

    id: int
    name: str = ""
    state: str = ""
    start_at: UtcTimestamp | None = None
    end_at: UtcTimestamp | None = None
    goal: str | None = None
    origin_board_id: int | None = None
