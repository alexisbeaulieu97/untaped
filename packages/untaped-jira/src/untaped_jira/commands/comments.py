"""``untaped jira issues comments``: read issue comments."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Group, Limit, Output, Positional, key
from untaped_jira.commands._jira import ISSUE_KEY, JiraCommand
from untaped_jira.keys import validate_issue_key
from untaped_jira.parse import parse_comment
from untaped_jira.records import Comment

_COLUMNS = (key(Comment).author, key(Comment).created_at, key(Comment).body)


class List(JiraCommand):
    """List the comments of one issue, oldest first."""

    key: Positional[str] = Field(description=ISSUE_KEY)
    limit: Limit
    output: Output[Comment] = Output(columns=_COLUMNS)

    def run(self) -> Iterator[Comment]:
        self.output.empty("No comments found.")
        key = validate_issue_key(self.key)
        with self.client() as client, self.ctx.diag.progress("Fetching comments"):
            rows = [
                parse_comment(each, issue_key=key)
                for each in client.list_comments(key, limit=self.limit.value)
            ]
        yield from rows


class Comments(Group):
    """Read issue comments."""

    commands = (List,)
