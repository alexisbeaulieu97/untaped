"""``untaped jira boards``: look up Jira Software boards."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from pydantic import Field

from untaped.sdk import Group, Limit, Output
from untaped_jira.commands._jira import JiraCommand
from untaped_jira.parse import parse_board
from untaped_jira.records import Board


class List(JiraCommand):
    """List visible Jira Software boards."""

    project: str | None = Field(None, description="Filter by project key or id.")
    name: str | None = Field(None, description="Filter by board name.")
    type: Literal["scrum", "kanban"] | None = Field(None, description="Filter by board type.")
    limit: Limit = Limit(default=50)
    output: Output[Board]

    def run(self) -> Iterator[Board]:
        self.output.empty("No boards match the filter.")
        with self.client() as client, self.ctx.diag.progress("Listing boards"):
            rows = [
                parse_board(board)
                for board in client.list_boards(
                    project_key_or_id=self.project,
                    name=self.name,
                    board_type=self.type,
                    limit=self.limit.value,
                )
            ]
        yield from rows


class Boards(Group):
    """Look up Jira Software boards."""

    commands = (List,)
