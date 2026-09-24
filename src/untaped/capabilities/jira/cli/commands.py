"""Cyclopts commands for Jira Data Center issue workflow."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from cyclopts import Parameter

from untaped.capabilities.jira.cli._client import current_jira_settings, open_client
from untaped.capabilities.jira.domain import (
    IssueOutcome,
    JiraIssueSearchFilters,
    browse_url,
    build_issue_payload,
    build_link_payload,
    build_transition_payload,
)
from untaped.capabilities.jira.errors import JiraError
from untaped.capability_api import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    LimitOption,
    OperationCancelledError,
    StdinOption,
    UsageError,
    YesOption,
    batch_apply,
    create_app,
    deprecated_alias,
    echo,
    emit,
    existing_file,
    finish,
    parse_json_pairs,
    parse_kv_pairs,
    q,
    raise_usage,
    read_identifiers,
    read_structured_file,
    report_errors,
    resolve_each,
    resolve_text_input,
)

if TYPE_CHECKING:
    from untaped.capability_api import UiContext

SetOption = Annotated[
    list[str] | None,
    Parameter(
        name="--set",
        help="Set a string field KEY=VALUE (repeatable).",
        negative="",
        consume_multiple=False,
    ),
]
SetJsonOption = Annotated[
    list[str] | None,
    Parameter(
        name="--set-json",
        help="Set a field from JSON KEY=JSON (repeatable).",
        negative="",
        consume_multiple=False,
    ),
]
ProjectFilterOption = Annotated[
    str | None,
    Parameter(name="--project", help="Project key (e.g. ABC) or name."),
]
StatusFilterOption = Annotated[
    str | None,
    Parameter(name="--status", help="Status name (e.g. 'In Progress')."),
]
TextFilterOption = Annotated[
    str | None,
    Parameter(name="--text", help="Full-text search across summary, description, comments."),
]
SprintFilterOption = Annotated[
    str | None,
    Parameter(
        name="--sprint",
        help="Sprint id or name, or openSprints()/futureSprints()/closedSprints().",
    ),
]
IssueKeyArgument = Annotated[str, Parameter(help="Issue key or id.")]
IssueKeysArgument = Annotated[
    list[str] | None,
    Parameter(help="Issue keys or ids (or pass --stdin).", negative=""),
]
# Default table columns: a compact row per issue, a detail view for one issue
# (comments get their own table), and a comment table.
ISSUE_TABLE_COLUMNS = [
    "key",
    "issue_type",
    "status",
    "priority",
    "assignee",
    "summary",
    "updated_at",
]
ISSUE_DETAIL_COLUMNS = [
    "key",
    "summary",
    "issue_type",
    "status",
    "resolution",
    "priority",
    "assignee",
    "reporter",
    "labels",
    "created_at",
    "updated_at",
    "url",
    "description",
]
COMMENT_TABLE_COLUMNS = ["issue_key", "author", "created_at", "body"]
# Pipe records that carry an issue ``key`` a consumer can act on.
ISSUE_KINDS = frozenset({"jira.issue", "jira.issue_outcome"})
OUTCOME_KIND = "jira.issue_outcome"

app = create_app(
    name="jira",
    help="Manage Jira Data Center issues from untaped.",
)
issues_app = create_app(name="issues", help="Manage Jira issues.")
projects_app = create_app(name="projects", help="Look up Jira projects.")
boards_app = create_app(name="boards", help="Look up Jira Software boards.")
sprints_app = create_app(name="sprints", help="Look up Jira Software sprints.")
comments_app = create_app(name="comments", help="Read issue comments.")
links_app = create_app(name="links", help="Link issues to each other.")


@app.command(name="whoami")
def whoami_command(
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show the authenticated Jira user."""

    from untaped.capabilities.jira.application import WhoAmI  # noqa: PLC0415

    with report_errors():
        with open_client() as (client, ui), ui.progress("Fetching authenticated user…"):
            row = WhoAmI(client)()
        emit(row, fmt=fmt, columns=columns, kind="jira.user")


@issues_app.command(name="get")
def issue_get_command(
    keys: IssueKeysArgument = None,
    /,
    *,
    comments: Annotated[
        bool,
        Parameter(name="--comments", negative="", help="Also fetch every comment."),
    ] = False,
    stdin: StdinOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Fetch one or more issues, with their description and optionally comments."""

    from untaped.capabilities.jira.application import GetIssue  # noqa: PLC0415

    with report_errors():
        resolved = read_identifiers(
            list(keys or []), stdin=stdin, id_field="key", accept_kinds=ISSUE_KINDS
        )
        single = not stdin and len(resolved) == 1
        with open_client() as (client, ui), ui.progress("Fetching issues…"):
            get_issue = GetIssue(client, comments=comments)
            if single:
                rows, any_failed = [get_issue(resolved[0])], False
            else:
                rows, any_failed = resolve_each(resolved, get_issue)
        if fmt == "table" and columns is None:
            columns = ISSUE_DETAIL_COLUMNS if single else ISSUE_TABLE_COLUMNS
        emit(rows[0] if single else rows, fmt=fmt, columns=columns, kind="jira.issue")
        if comments and fmt == "table":
            emit(
                [comment for row in rows for comment in row.comments or []],
                fmt=fmt,
                columns=COMMENT_TABLE_COLUMNS,
                kind="jira.comment",
                empty="No comments found.",
            )
        finish(any_failed)


@comments_app.command(name="list")
def comment_list_command(
    key: IssueKeyArgument,
    /,
    *,
    limit: LimitOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List the comments of one issue, oldest first."""

    from untaped.capabilities.jira.application import ListComments  # noqa: PLC0415

    with report_errors():
        with open_client() as (client, ui), ui.progress("Fetching comments…"):
            rows = ListComments(client)(key, limit=limit)
        table_columns = columns or (COMMENT_TABLE_COLUMNS if fmt == "table" else None)
        emit(rows, fmt=fmt, columns=table_columns, kind="jira.comment", empty="No comments found.")


@issues_app.command(name="search")
def issue_search_command(
    *,
    jql: Annotated[str | None, Parameter(name="--jql", help="Raw JQL base query.")] = None,
    project: ProjectFilterOption = None,
    assignee: Annotated[
        str | None,
        Parameter(name="--assignee", help="Assignee username, or @me for yourself."),
    ] = None,
    status: StatusFilterOption = None,
    text: TextFilterOption = None,
    sprint: SprintFilterOption = None,
    limit: LimitOption = 50,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Search issues with JQL plus common shortcuts."""

    from untaped.capabilities.jira.application import SearchIssues  # noqa: PLC0415

    with report_errors():
        filters = JiraIssueSearchFilters(
            default_jql=current_jira_settings().assigned_jql,
            raw_jql=jql,
            project=project,
            assignee=assignee,
            status=status,
            text=text,
            sprint=sprint,
        )
        with open_client() as (client, ui), ui.progress("Querying Jira issues…"):
            rows = SearchIssues(client)(filters, limit=limit)
        emit(rows, fmt=fmt, columns=columns, kind="jira.issue", empty="No issues match the query.")


@issues_app.command(name="assigned")
def issue_assigned_command(
    *,
    jql: Annotated[
        str | None,
        Parameter(name="--jql", help="Extra JQL ANDed with jira.assigned_jql."),
    ] = None,
    project: ProjectFilterOption = None,
    status: StatusFilterOption = None,
    text: TextFilterOption = None,
    sprint: SprintFilterOption = None,
    limit: LimitOption = 50,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List issues assigned to the authenticated Jira user."""

    from untaped.capabilities.jira.application import SearchIssues  # noqa: PLC0415

    with report_errors():
        settings = current_jira_settings()
        filters = JiraIssueSearchFilters(
            scope_jql=settings.assigned_jql,
            raw_jql=_nonblank_jql(jql),
            project=project,
            status=status,
            text=text,
            sprint=sprint,
        )
        with open_client() as (client, ui), ui.progress("Querying assigned issues…"):
            rows = SearchIssues(client)(filters, limit=limit)
        emit(rows, fmt=fmt, columns=columns, kind="jira.issue", empty="No issues assigned to you.")


def _nonblank_jql(jql: str | None) -> str | None:
    if jql is None:
        return None
    stripped = jql.strip()
    if not stripped:
        raise UsageError("--jql must not be blank")
    return stripped


def _username(assignee: str) -> str:
    """``assignee`` itself, or the authenticated user's name for ``@me`` (one GET)."""
    if assignee != "@me":
        return assignee
    from untaped.capabilities.jira.application import WhoAmI  # noqa: PLC0415

    with open_client() as (client, ui), ui.progress("Fetching authenticated user…"):
        name = WhoAmI(client)().name
    if not name:
        raise JiraError("the authenticated Jira user has no username")
    return name


def _show_request(method: str, path: str, body: object) -> None:
    """Print the REST request a write would send (stderr; stdout stays data)."""
    echo(f"{method} {path}", err=True)
    echo(json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True), err=True)


def _confirm_request(
    ui: UiContext, *, verb: str, method: str, path: str, body: object, yes: bool
) -> None:
    """Show the request and ask before sending it; ``--yes`` skips both."""
    if yes:
        return
    with ui.terminal(refusal=f"{verb} requires --yes when not interactive"):
        _show_request(method, path, body)
        if not ui.confirm("Send this request to Jira?"):
            raise OperationCancelledError


@issues_app.command(name="create")
def issue_create_command(
    *,
    template: Annotated[
        Path | None,
        Parameter(
            name="--template",
            validator=existing_file,
            help="Jira-shaped YAML/JSON payload file; flags override its fields.",
        ),
    ] = None,
    project: Annotated[
        str | None,
        Parameter(name="--project", help="Project key; defaults to jira.default_project."),
    ] = None,
    issue_type: Annotated[
        str | None, Parameter(name="--issue-type", help="Issue type name (e.g. Bug, Task).")
    ] = None,
    summary: Annotated[str | None, Parameter(name="--summary", help="Issue summary.")] = None,
    description: Annotated[
        str | None, Parameter(name="--description", help="Issue description text.")
    ] = None,
    set_fields: SetOption = None,
    set_json: SetJsonOption = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Create one issue from flags and an optional Jira-shaped template."""

    from untaped.capabilities.jira.application import CreateIssue  # noqa: PLC0415

    with report_errors():
        settings = current_jira_settings()
        base = read_structured_file(template) if template is not None else {}
        payload = build_issue_payload(
            base=base,
            project=project or settings.default_project,
            issue_type=issue_type,
            summary=summary,
            description=description,
            fields=parse_kv_pairs(set_fields, flag="--set"),
            json_fields=parse_json_pairs(set_json, flag="--set-json"),
        )
        path = f"{settings.api_prefix}/issue"
        if dry_run:
            _show_request("POST", path, payload)
            emit(IssueOutcome(action="planned"), fmt=fmt, columns=columns, kind=OUTCOME_KIND)
            return
        with open_client() as (client, ui):
            _confirm_request(ui, verb="create", method="POST", path=path, body=payload, yes=yes)
            with ui.progress("Creating issue…"):
                row = CreateIssue(client, base_url=settings.base_url)(payload)
        emit(row, fmt=fmt, columns=columns, kind=OUTCOME_KIND)


@issues_app.command(name="patch")
def issue_patch_command(
    key: IssueKeyArgument,
    /,
    *,
    body_file: Annotated[
        Path | None,
        Parameter(
            name="--body-file",
            validator=existing_file,
            help="Jira-shaped YAML/JSON payload file (fields/update); flags override it.",
        ),
    ] = None,
    summary: Annotated[str | None, Parameter(name="--summary", help="New issue summary.")] = None,
    description: Annotated[
        str | None, Parameter(name="--description", help="New issue description text.")
    ] = None,
    assignee: Annotated[
        str | None,
        Parameter(name="--assignee", help="Assign to this username, or @me for yourself."),
    ] = None,
    unassign: Annotated[
        bool, Parameter(name="--unassign", negative="", help="Remove the assignee.")
    ] = False,
    set_fields: SetOption = None,
    set_json: SetJsonOption = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Update fields of one issue (including its assignee) from flags or a body file."""

    from untaped.capabilities.jira.application import PatchIssue  # noqa: PLC0415

    with report_errors():
        settings = current_jira_settings()
        base = read_structured_file(body_file) if body_file is not None else {}
        payload = build_issue_payload(
            base=base,
            summary=summary,
            description=description,
            fields=parse_kv_pairs(set_fields, flag="--set"),
            json_fields=parse_json_pairs(set_json, flag="--set-json"),
        )
        if assignee is not None and unassign:
            raise_usage("pass either --assignee or --unassign, not both")
        if unassign:
            payload["fields"]["assignee"] = None
        if not payload.get("fields") and not payload.get("update") and assignee is None:
            raise_usage(
                "nothing to update: pass --summary, --description, --assignee, --unassign, "
                "--set, --set-json, or a --body-file with fields/update"
            )
        if assignee is not None:
            payload["fields"]["assignee"] = {"name": _username(assignee)}
        path = f"{settings.api_prefix}/issue/{key}"
        if dry_run:
            _show_request("PUT", path, payload)
            planned = IssueOutcome(
                action="planned", key=key, url=browse_url(settings.base_url, key)
            )
            emit(planned, fmt=fmt, columns=columns, kind=OUTCOME_KIND)
            return
        with open_client() as (client, ui):
            _confirm_request(ui, verb="patch", method="PUT", path=path, body=payload, yes=yes)
            with ui.progress("Updating issue…"):
                row = PatchIssue(client, base_url=settings.base_url)(key, payload)
        emit(row, fmt=fmt, columns=columns, kind=OUTCOME_KIND)


@issues_app.command(name="comment")
def issue_comment_command(
    key: IssueKeyArgument,
    /,
    *,
    body: Annotated[str | None, Parameter(name="--body", help="Comment body.")] = None,
    body_file: Annotated[
        Path | None,
        Parameter(
            name="--body-file",
            validator=existing_file,
            help="Read the comment body from a file.",
        ),
    ] = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Add a comment to one issue (the body may also be piped on stdin)."""

    from untaped.capabilities.jira.application import AddComment  # noqa: PLC0415

    with report_errors():
        settings = current_jira_settings()
        resolved_body = resolve_text_input(value=body, file=body_file, what="body")
        path = f"{settings.api_prefix}/issue/{key}/comment"
        request = {"body": resolved_body}
        if dry_run:
            _show_request("POST", path, request)
            planned = IssueOutcome(
                action="planned", key=key, url=browse_url(settings.base_url, key)
            )
            emit(planned, fmt=fmt, columns=columns, kind=OUTCOME_KIND)
            return
        with open_client() as (client, ui):
            _confirm_request(ui, verb="comment", method="POST", path=path, body=request, yes=yes)
            with ui.progress("Adding comment…"):
                row = AddComment(client, base_url=settings.base_url)(key, resolved_body)
        emit(row, fmt=fmt, columns=columns, kind=OUTCOME_KIND)


@issues_app.command(name="transitions")
def issue_transitions_command(
    key: IssueKeyArgument,
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List available workflow transitions for one issue."""

    from untaped.capabilities.jira.application import ListTransitions  # noqa: PLC0415

    with report_errors():
        with open_client() as (client, ui), ui.progress("Fetching available transitions…"):
            rows = ListTransitions(client)(key)
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="jira.transition",
            empty="No transitions available for this issue.",
        )


@issues_app.command(name="transition")
def issue_transition_command(
    keys: IssueKeysArgument = None,
    /,
    *,
    to: Annotated[str | None, Parameter(name="--to", help="Transition name.")] = None,
    transition_id: Annotated[str | None, Parameter(name="--id", help="Transition id.")] = None,
    comment: Annotated[
        str | None, Parameter(name="--comment", help="Add this comment with the transition.")
    ] = None,
    resolution: Annotated[
        str | None,
        Parameter(name="--resolution", help="Set this resolution (e.g. Fixed, Done)."),
    ] = None,
    stdin: StdinOption = False,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Apply one workflow transition, by name or id, to one or more issues."""

    from untaped.capabilities.jira.application import TransitionIssue  # noqa: PLC0415

    with report_errors():
        TransitionIssue.check_selector(transition_id, to)
        resolved = read_identifiers(
            list(keys or []), stdin=stdin, id_field="key", accept_kinds=ISSUE_KINDS
        )
        settings = current_jira_settings()
        single = not stdin and len(resolved) == 1
        with open_client() as (client, ui):
            transition = TransitionIssue(client, base_url=settings.base_url)
            with ui.progress("Resolving transitions…"):
                plans, resolve_failed = resolve_each(
                    resolved,
                    lambda key: (
                        key,
                        transition.resolve(key, transition_id=transition_id, transition_name=to),
                    ),
                )

            def preview(rows: Sequence[dict[str, object]]) -> None:
                for row in rows:
                    _show_request(
                        "POST",
                        f"{settings.api_prefix}/issue/{row['key']}/transitions",
                        build_transition_payload(
                            str(row["transition_id"]), comment=comment, resolution=resolution
                        ),
                    )

            outcome = batch_apply(
                plans,
                lambda plan: transition(*plan, comment=comment, resolution=resolution),
                verb="transition",
                noun="issue",
                label=lambda plan: plan[0],
                describe=lambda plan: {"key": plan[0], "transition_id": plan[1]},
                ui=ui,
                destructive=True,
                assume_yes=yes,
                preview_only=dry_run,
                preview=preview,
            )
        if outcome.cancelled:
            finish(outcome)
        if dry_run:
            preview(outcome.planned_rows)
            rows: list[Any] = [
                IssueOutcome(
                    action="planned",
                    key=key,
                    url=browse_url(settings.base_url, key),
                    transition_id=resolved_id,
                )
                for key, resolved_id in plans
            ]
        else:
            rows = [result for _, result in outcome.results]
        if single and rows:
            emit(rows[0], fmt=fmt, columns=columns, kind=OUTCOME_KIND)
        else:
            emit(rows, fmt=fmt, columns=columns, kind=OUTCOME_KIND)
        finish(resolve_failed or outcome.any_failed)


@links_app.command(name="create")
def link_create_command(
    key: IssueKeyArgument,
    link_type: Annotated[str, Parameter(help="Link type name (e.g. Blocks, Relates).")],
    other: Annotated[str, Parameter(help="Issue key the first issue links to.")],
    /,
    *,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Link KEY to OTHER, read as "KEY <outward phrase> OTHER" (OPS-1 Blocks OPS-2)."""

    from untaped.capabilities.jira.application import LinkIssues  # noqa: PLC0415

    with report_errors():
        settings = current_jira_settings()
        path = f"{settings.api_prefix}/issueLink"
        payload = build_link_payload(key, link_type, other)
        if dry_run or not yes:
            # The REST field names read backwards; say the direction in words.
            echo(f"reads as: {key} <outward phrase of {q(link_type)}> {other}", err=True)
        if dry_run:
            _show_request("POST", path, payload)
            planned = IssueOutcome(
                action="planned",
                key=key,
                url=browse_url(settings.base_url, key),
                link_type=link_type,
                linked_key=other,
            )
            emit(planned, fmt=fmt, columns=columns, kind=OUTCOME_KIND)
            return
        with open_client() as (client, ui):
            _confirm_request(ui, verb="link", method="POST", path=path, body=payload, yes=yes)
            with ui.progress("Linking issues…"):
                row = LinkIssues(client, base_url=settings.base_url)(key, link_type, other)
        emit(row, fmt=fmt, columns=columns, kind=OUTCOME_KIND)


@projects_app.command(name="list")
def project_list_command(
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List visible projects."""

    from untaped.capabilities.jira.application import ListProjects  # noqa: PLC0415

    with report_errors():
        with open_client() as (client, ui), ui.progress("Listing projects…"):
            rows = ListProjects(client)()
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="jira.project",
            empty="No projects are visible to you.",
        )


@projects_app.command(name="get")
def project_get_command(
    key: Annotated[str, Parameter(help="Project key or id.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Fetch one project."""

    from untaped.capabilities.jira.application import GetProject  # noqa: PLC0415

    with report_errors():
        with open_client() as (client, ui), ui.progress("Fetching project…"):
            row = GetProject(client)(key)
        emit(row, fmt=fmt, columns=columns, kind="jira.project")


@boards_app.command(name="list")
def board_list_command(
    *,
    project: Annotated[
        str | None,
        Parameter(name="--project", help="Filter by project key or id."),
    ] = None,
    name: Annotated[str | None, Parameter(name="--name", help="Filter by board name.")] = None,
    board_type: Annotated[
        Literal["scrum", "kanban"] | None,
        Parameter(name="--type", help="Filter by board type."),
    ] = None,
    limit: LimitOption = 50,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List visible Jira Software boards."""

    from untaped.capabilities.jira.application import ListBoards  # noqa: PLC0415

    with report_errors():
        with open_client() as (client, ui), ui.progress("Listing boards…"):
            rows = ListBoards(client)(
                project_key_or_id=project,
                name=name,
                board_type=board_type,
                limit=limit,
            )
        emit(rows, fmt=fmt, columns=columns, kind="jira.board", empty="No boards match the filter.")


@sprints_app.command(name="list")
def sprint_list_command(
    *,
    board_id: Annotated[int | None, Parameter(name="--board-id", help="Board id.")] = None,
    state: Annotated[
        str | None,
        Parameter(name="--state", help="Sprint state filter, e.g. active,future."),
    ] = None,
    limit: LimitOption = 50,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List sprints for a board."""

    from untaped.capabilities.jira.application import ListSprints  # noqa: PLC0415

    with report_errors():
        settings = current_jira_settings()
        with open_client() as (client, ui), ui.progress("Listing sprints…"):
            rows = ListSprints(client, default_board_id=settings.default_board_id)(
                board_id=board_id,
                state=state,
                limit=limit,
            )
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="jira.sprint",
            empty="No sprints found for this board.",
        )


issues_app.command(comments_app, name="comments")
issues_app.command(links_app, name="links")
app.command(issues_app, name="issues")
app.command(projects_app, name="projects")
app.command(boards_app, name="boards")
app.command(sprints_app, name="sprints")

# Old spellings stay as hidden, warning aliases until 7.0.
deprecated_alias(app, "me", "whoami")
deprecated_alias(app, "issue", "issues")
deprecated_alias(app, "project", "projects")
deprecated_alias(app, "board", "boards")
deprecated_alias(app, "sprint", "sprints")
deprecated_alias(issues_app, "edit", "patch")
for _command in ("create", "patch"):
    deprecated_alias(issues_app[_command], "--field", "--set")
    deprecated_alias(issues_app[_command], "--json-field", "--set-json")
