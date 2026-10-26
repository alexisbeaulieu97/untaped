"""What every jira command shares: its client, and how a write previews, asks and sends."""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager

from untaped.sdk import Command, Confirm, Output
from untaped_jira.changes import needs_confirmation
from untaped_jira.client import JiraClient
from untaped_jira.keys import validate_issue_key
from untaped_jira.records import IssueOutcome
from untaped_jira.settings import JiraSettings

#: One write request as previewed: method, path and its readable change lines.
PreviewRequest = tuple[str, str, list[str]]
Preview = Sequence[PreviewRequest] | Callable[[JiraClient], Sequence[PreviewRequest]]

#: A compact row per issue; a detail view prints one issue's own fields.
ISSUE_COLUMNS = ("key", "issue_type", "status", "priority", "assignee", "summary", "updated_at")
ISSUE_DETAIL_COLUMNS = (
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
)

#: How a positional issue key reads in help.
#: A write's rows: which issue, what happened to it, and where to open it.
OUTCOME_COLUMNS = ("key", "action", "url")
ISSUE_KEY = "Issue key (PROJECT-123, in any case) or numeric id."


class JiraCommand(Command[JiraSettings]):
    """A command that talks to Jira through the profile's ``jira`` settings."""

    @contextmanager
    def client(self) -> Iterator[JiraClient]:
        """A Jira client for this run, closed when the block ends."""
        with JiraClient(self.settings, http=self.ctx.http) as client:
            yield client


class JiraWrite(JiraCommand):
    """A command that sends one write: it previews, asks per ``jira.confirm``, then sends."""

    confirm: Confirm
    output: Output[IssueOutcome] = Output(columns=OUTCOME_COLUMNS)

    def send(
        self,
        preview: Preview,
        send: Callable[[JiraClient], IssueOutcome],
        *,
        destructive: bool,
        planned: IssueOutcome,
        progress: str,
    ) -> IssueOutcome:
        """Preview the write (``--dry-run``), or confirm it per ``jira.confirm`` and send it.

        A callable ``preview`` reads the issue's current values, so it runs
        only when a preview is shown; a static one needs no client.
        """
        with ExitStack() as stack:
            opened: list[JiraClient] = []

            def client() -> JiraClient:
                if not opened:
                    opened.append(stack.enter_context(self.client()))
                return opened[0]

            def lines() -> list[str]:
                if not callable(preview):
                    return request_lines(preview)
                with self.ctx.diag.progress("Reading current values"):
                    return request_lines(preview(client()))

            needed = needs_confirmation(self.settings.confirm, destructive=destructive)
            if not self.confirm("Send this request to Jira?", preview=lines, needed=needed):
                return planned
            with self.ctx.diag.progress(progress):
                return send(client())


def request_lines(requests: Sequence[PreviewRequest]) -> list[str]:
    """Each request a write would send, then its readable changes indented under it."""
    return [
        line
        for method, path, changes in requests
        for line in (f"{method} {path}", *(textwrap.indent(each, "  ") for each in changes))
    ]


def issue_keys(values: Sequence[str]) -> list[str]:
    """Each issue key checked: ``PROJECT-123`` in any case, or a numeric id."""
    return [validate_issue_key(value) for value in values]
