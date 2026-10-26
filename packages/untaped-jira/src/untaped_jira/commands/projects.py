"""``untaped jira projects``: look up projects."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Group, Output, Positional
from untaped_jira.commands._jira import JiraCommand
from untaped_jira.keys import validate_project_key
from untaped_jira.parse import parse_project
from untaped_jira.records import Project


class List(JiraCommand):
    """List visible projects."""

    output: Output[Project]

    def run(self) -> Iterator[Project]:
        self.output.empty("No projects are visible to you.")
        with self.client() as client, self.ctx.diag.progress("Listing projects"):
            rows = [parse_project(project) for project in client.list_projects()]
        yield from rows


class Get(JiraCommand):
    """Fetch one project."""

    key: Positional[str] = Field(description="Project key (in any case) or numeric id.")
    output: Output[Project]

    def run(self) -> Project:
        key = validate_project_key(self.key)
        with self.client() as client, self.ctx.diag.progress("Fetching project"):
            return parse_project(client.get_project(key))


class Projects(Group):
    """Look up Jira projects."""

    commands = (List, Get)
