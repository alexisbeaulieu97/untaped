"""What every jira command shares: its client, and how a write previews, asks and sends."""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager

from untaped.sdk import Command, Confirm, DryRun, Output
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
ISSUE_KEY = "Issue key (PROJECT-123, in any case) or numeric id."


class JiraCommand(Command[JiraSettings]):
    """A command that talks to Jira through the profile's ``jira`` settings."""

    @contextmanager
    def client(self) -> Iterator[JiraClient]:
        """A Jira client for this run, closed when the block ends."""
        with JiraClient(self.settings, http=self.ctx.http) as client:
            yield client


class JiraWrite(JiraCommand):
    """A command that sends one write: it previews, goes ahead (:meth:`_go_ahead`), then sends."""

    output: Output[IssueOutcome]

    def _go_ahead(self, preview: Callable[[], list[str]], *, replaces: bool) -> bool:
        """Whether to send; ``False`` after ``--dry-run`` printed ``preview``."""
        raise NotImplementedError

    def send(
        self,
        preview: Preview,
        send: Callable[[JiraClient], IssueOutcome],
        *,
        planned: IssueOutcome,
        progress: str,
        replaces: bool = False,
    ) -> IssueOutcome:
        """Preview the write (``--dry-run``), or go ahead and send it.

        A callable ``preview`` reads the issue's current values, so it runs
        only when a preview is shown; a static one needs no client.
        ``replaces`` says the write can replace or remove what the issue holds.
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

            if not self._go_ahead(lines, replaces=replaces):
                return planned
            with self.ctx.diag.progress(progress):
                return send(client())


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
        if not self.dry_run:
            return True
        for line in preview():
            self.ctx.diag.info(line, unmuted=True)
        return False


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
