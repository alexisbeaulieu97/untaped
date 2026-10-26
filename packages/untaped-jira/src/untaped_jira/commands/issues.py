"""``untaped jira issues``: find and read issues; the writes are in :mod:`.issue_writes`."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Group, Limit, Output, Positional, Stdin, UntapedError, UsageError
from untaped_jira.client import JiraClient
from untaped_jira.commands._jira import (
    ISSUE_COLUMNS,
    ISSUE_DETAIL_COLUMNS,
    ISSUE_KEY,
    JiraCommand,
    issue_keys,
)
from untaped_jira.commands.comments import Comments
from untaped_jira.commands.issue_writes import Comment, Create, Patch, Transition
from untaped_jira.commands.links import Links
from untaped_jira.keys import validate_issue_key
from untaped_jira.parse import parse_comment, parse_issue, parse_transition
from untaped_jira.records import Issue, IssueOutcome
from untaped_jira.records import Transition as TransitionRow
from untaped_jira.search import JiraIssueSearchFilters


class Get(JiraCommand):
    """Fetch one or more issues, with their description and optionally comments.

    Besides the fields a search fills, it fills the detail fields
    (``reporter`` to ``links``); ``comments`` stays ``null`` without
    ``--comments``. A linked issue carries only what the link embeds (its key,
    summary and status); get its key for the rest. A table prints the links
    (when there are some), then the comments, as their own tables after the
    issues.

    One issue prints as a key: value view in a table and as one JSON object;
    several print as a table or a JSON array. ``--stdin`` reads bare keys or
    ``jira.issue`` and ``jira.issue_outcome`` records from ``--format pipe``;
    any other record kind exits 2. A key that fails prints ``error: KEY: ...``
    and the rest still print; the exit code is the most severe failure.
    """

    keys: Positional[list[str]] = Field(
        default_factory=list, description="Issue keys (PROJECT-123, in any case) or numeric ids."
    )
    comments: bool = Field(False, description="Also fetch every comment.")
    stdin: Stdin[Issue | IssueOutcome]
    output: Output[Issue] = Output(columns=ISSUE_COLUMNS)

    def run(self) -> Issue | Iterator[Issue]:
        keys = issue_keys(self.stdin.identifiers(self.keys))
        if len(self.keys) != 1:
            return self._each(keys)
        self.output.columns[:] = ISSUE_DETAIL_COLUMNS
        with self.client() as client, self.ctx.diag.progress("Fetching issues"):
            return self._issue(client, keys[0])

    def _each(self, keys: list[str]) -> Iterator[Issue]:
        rows: list[Issue] = []
        with self.client() as client, self.ctx.diag.progress("Fetching issues"):
            for key in keys:
                try:
                    rows.append(self._issue(client, key))
                except UntapedError as exc:
                    self.ctx.diag.failure(exc, item=key)
        yield from rows

    def _issue(self, client: JiraClient, key: str) -> Issue:
        issue = parse_issue(client.get_issue(key), detail=True)
        if not issue.links and self.output.format == "table":
            issue = issue.model_copy(update={"links": None})  # a table shows links only when some
        if not self.comments:
            return issue
        comments = [parse_comment(each, issue_key=issue.key) for each in client.list_comments(key)]
        return issue.model_copy(update={"comments": comments})


class _Search(JiraCommand):
    """The shortcuts both searches take; each ANDs them onto its base query."""

    project: str | None = Field(None, description="Project key (e.g. ABC) or name.")
    status: str | None = Field(None, description="Status name (e.g. 'In Progress').")
    text: str | None = Field(
        None, description="Full-text search across summary, description, comments."
    )
    sprint: str | None = Field(
        None, description="Sprint id or name, or openSprints()/futureSprints()/closedSprints()."
    )
    limit: Limit = Limit(default=50)

    def _found(self, filters: JiraIssueSearchFilters, progress: str) -> Iterator[Issue]:
        with self.client() as client, self.ctx.diag.progress(progress):
            rows = [
                parse_issue(issue)
                for issue in client.search_issues(filters.to_jql(), limit=self.limit.value)
            ]
        yield from rows


class Search(_Search):
    """Search issues with JQL plus common shortcuts.

    ``--jql`` and the shortcut flags are ANDed together. With none of them it
    searches ``jira.assigned_jql`` (by default your unresolved issues), not
    every issue. An ``ORDER BY`` in ``--jql`` replaces the default
    ``updated DESC``. Rows fill the fields from ``key`` to ``priority``; the
    detail fields stay ``null`` (``issues get`` fills them). Jira's 429 and 503
    answers are retried.

    Examples
    --------
    ```console
    $ untaped jira issues search --jql 'project = OPS AND labels = infra ORDER BY created DESC'
    ```
    """

    jql: str | None = Field(None, description="Raw JQL base query.")
    assignee: str | None = Field(None, description="Assignee username, or @me for yourself.")
    output: Output[Issue] = Output(columns=ISSUE_COLUMNS)

    def run(self) -> Iterator[Issue]:
        self.output.empty("No issues match the query.")
        filters = JiraIssueSearchFilters(
            default_jql=self.settings.assigned_jql,
            raw_jql=self.jql,
            project=self.project,
            assignee=self.assignee,
            status=self.status,
            text=self.text,
            sprint=self.sprint,
        )
        return self._found(filters, "Querying Jira issues")


class Assigned(_Search):
    """List issues assigned to the authenticated Jira user.

    It always starts from ``jira.assigned_jql`` (by default your unresolved
    issues); ``--jql`` and the shortcut flags are ANDed onto it, so they only
    narrow it. An ``ORDER BY`` in ``--jql`` replaces the default
    ``updated DESC``. Jira's 429 and 503 answers are retried.

    Examples
    --------
    ```console
    $ untaped jira issues assigned --status 'In Progress' --sprint 'openSprints()'
    ```
    """

    jql: str | None = Field(None, description="Extra JQL ANDed with jira.assigned_jql.")
    output: Output[Issue] = Output(columns=[c for c in ISSUE_COLUMNS if c != "assignee"])

    def run(self) -> Iterator[Issue]:
        self.output.empty("No issues assigned to you.")
        jql = None if self.jql is None else self.jql.strip()
        if jql == "":
            raise UsageError("--jql must not be blank")
        filters = JiraIssueSearchFilters(
            scope_jql=self.settings.assigned_jql,
            raw_jql=jql,
            project=self.project,
            status=self.status,
            text=self.text,
            sprint=self.sprint,
        )
        return self._found(filters, "Querying assigned issues")


class Transitions(JiraCommand):
    """List available workflow transitions for one issue.

    It lists what the issue offers from its current status; names and ids
    differ between workflows and statuses.
    """

    key: Positional[str] = Field(description=ISSUE_KEY)
    output: Output[TransitionRow]

    def run(self) -> Iterator[TransitionRow]:
        self.output.empty("No transitions available for this issue.")
        key = validate_issue_key(self.key)
        with self.client() as client, self.ctx.diag.progress("Fetching available transitions"):
            rows = [parse_transition(each) for each in client.list_transitions(key)]
        yield from rows


class Issues(Group):
    """Manage Jira issues.

    ## The preview

    The preview lists each request a write sends (`METHOD path`), then one
    `field: old → new` line per change. A field's long text is cut to 60
    characters in the preview only; the full value is sent. A comment prints
    whole.

    - `--dry-run` prints the preview on stderr, emits a `jira.issue_outcome`
      with `action` `planned`, and sends nothing, even with `--yes`.
    - Patch and transition previews read the issue's current values, so they
      need working credentials; a patch reads the fields it sets and, when
      assigning, the assignee. A patch preview fails when the issue can't be
      read. A transition preview shows `(unknown)` for a value it can't read,
      and `(not available from this status)` for an `--id` the issue doesn't
      offer.
    - Create, comment and link previews make no request.

    ## Which writes ask first

    A write asks first when it can replace or remove what an issue holds: every
    `issues transition`, and an `issues patch` that sets a field, changes the
    assignee, or has an `update` operation other than `add`; an add-only patch
    doesn't ask. `issues create`, `issues comment` and `issues links create`
    only add, so they never ask and take `--dry-run` without `--yes`.

    When a write asks, it shows the preview with the question. `--yes` skips
    both the question and the preview's reads.

    ## What a write emits

    Each write emits a `jira.issue_outcome` record;
    `untaped plugin schema jira.issue_outcome` describes it.

    Writes are sent once, never resent: Jira's 429 and 503 answers are retried
    only for reads. A write that exits 5 may still have landed:
    `untaped jira issues get KEY` shows whether it did.
    """

    commands = (
        Get,
        Search,
        Assigned,
        Create,
        Patch,
        Comment,
        Transitions,
        Transition,
        Comments,
        Links,
    )
