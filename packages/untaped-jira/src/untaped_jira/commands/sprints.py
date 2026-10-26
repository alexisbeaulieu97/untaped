"""``untaped jira sprints``: look up Jira Software sprints."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Group, Limit, Output, UsageError
from untaped_jira.commands._jira import JiraCommand
from untaped_jira.parse import parse_sprint
from untaped_jira.records import Sprint


class List(JiraCommand):
    """List sprints for a board."""

    board_id: int | None = Field(None, description="Board id; defaults to jira.default_board_id.")
    state: str | None = Field(None, description="Sprint state filter, e.g. active,future.")
    limit: Limit = Limit(default=50)
    output: Output[Sprint]

    def run(self) -> Iterator[Sprint]:
        self.output.empty("No sprints found for this board.")
        board = self.board_id or self.settings.default_board_id
        if board is None:
            raise UsageError(
                "board id is required (pass --board-id or configure jira.default_board_id)"
            )
        with self.client() as client, self.ctx.diag.progress("Listing sprints"):
            rows = [
                parse_sprint(sprint)
                for sprint in client.list_sprints(board, state=self.state, limit=self.limit.value)
            ]
        yield from rows


class Sprints(Group):
    """Look up Jira Software sprints."""

    commands = (List,)
