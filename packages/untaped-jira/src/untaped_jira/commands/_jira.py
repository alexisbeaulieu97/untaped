"""What every jira command shares: its port to Jira, and how a write previews, asks and sends."""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Sequence

from untaped.sdk import Command, Confirm, DryRun, Output, key
from untaped_jira.keys import validate_issue_key
from untaped_jira.ports import JiraApi
from untaped_jira.records import Issue, IssueOutcome
from untaped_jira.server_api import SERVER_API
from untaped_jira.settings import JiraSettings

#: One change a write would make, as previewed: what it does (``update ABC-1``) and its lines.
PreviewRequest = tuple[str, list[str]]
Preview = Sequence[PreviewRequest] | Callable[[JiraApi], Sequence[PreviewRequest]]

#: A compact row per issue; a detail view prints one issue's own fields.
ISSUE_COLUMNS = (
    key(Issue).key,
    key(Issue).issue_type,
    key(Issue).status,
    key(Issue).priority,
    key(Issue).assignee,
    key(Issue).summary,
    key(Issue).updated_at,
)
ISSUE_DETAIL_COLUMNS = (
    key(Issue).key,
    key(Issue).summary,
    key(Issue).issue_type,
    key(Issue).status,
    key(Issue).resolution,
    key(Issue).priority,
    key(Issue).assignee,
    key(Issue).reporter,
    key(Issue).labels,
    key(Issue).created_at,
    key(Issue).updated_at,
    key(Issue).url,
    key(Issue).description,
)

#: How a positional issue key reads in help.
ISSUE_KEY = "Issue key (PROJECT-123, in any case) or numeric id."


class JiraCommand(Command[JiraSettings]):
    """A command that talks to Jira through the profile's ``jira`` settings."""

    def client(self) -> JiraApi:
        """The port to the profile's Jira: nothing is sent until a call, none names a version."""
        return SERVER_API.port(self.settings)


class JiraWrite(JiraCommand):
    """A command that sends one write: it previews, goes ahead (:meth:`_go_ahead`), then sends."""

    output: Output[IssueOutcome]

    def _go_ahead(self, preview: Callable[[], list[str]], *, replaces: bool) -> bool:
        """Whether to send; ``False`` after ``--dry-run`` printed ``preview``."""
        raise NotImplementedError

    def send(
        self,
        preview: Preview,
        send: Callable[[JiraApi], IssueOutcome],
        *,
        planned: IssueOutcome,
        progress: str,
        replaces: bool = False,
    ) -> IssueOutcome:
        """Preview the write (``--dry-run``), or go ahead and send it.

        A callable ``preview`` reads the issue's current values, so it runs
        only when a preview is shown; a static one sends nothing.
        ``replaces`` says the write can replace or remove what the issue holds.
        """
        jira = self.client()

        def lines() -> list[str]:
            if not callable(preview):
                return request_lines(preview)
            with self.ctx.diag.progress("Reading current values"):
                return request_lines(preview(jira))

        if not self._go_ahead(lines, replaces=replaces):
            return planned
        with self.ctx.diag.progress(progress):
            return send(jira)


class AsksFirst(JiraWrite):
    """A write that asks before it replaces or removes what an issue holds."""

    confirm: Confirm

    def _go_ahead(self, preview: Callable[[], list[str]], *, replaces: bool) -> bool:
        return self.confirm("Send this request to Jira?", preview=preview, needed=replaces)


class SendsDirectly(JiraWrite):
    """A write that only adds to Jira, so it never asks; ``--dry-run`` previews it."""

    dry_run: DryRun

    def _go_ahead(self, preview: Callable[[], list[str]], *, replaces: bool) -> bool:
        del replaces
        return self.dry_run.go_ahead(preview)


def request_lines(requests: Sequence[PreviewRequest]) -> list[str]:
    """Each change a write would make (``update ABC-1``), then its lines indented under it."""
    return [
        line
        for what, changes in requests
        for line in (what, *(textwrap.indent(each, "  ") for each in changes))
    ]


def issue_keys(values: Sequence[str]) -> list[str]:
    """Each issue key checked: ``PROJECT-123`` in any case, or a numeric id."""
    return [validate_issue_key(value) for value in values]
