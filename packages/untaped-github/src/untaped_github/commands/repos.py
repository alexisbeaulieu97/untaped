"""``untaped github repos``: the repository inventory of org and team scopes."""

from __future__ import annotations

import re
from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import Group, Limit, Output, Positional, UsageError, plural
from untaped_github.application import ListRepos, RepoListFilters
from untaped_github.commands._github import (
    ARCHIVED,
    FORK,
    FORKS,
    ForkMode,
    GithubCommand,
    Scope,
    team_scopes,
)
from untaped_github.domain import ArchivedMode
from untaped_github.records import Repo


class List(GithubCommand):
    """List repositories from additive org/team inventory scopes.

    The complete inventory of its scopes, matched by name locally; ``search
    repos`` ranks instead.

    Examples
    --------
    ```console
    $ untaped github repos list 'svc-*' --org acme --format pipe
    ```
    """

    pattern: Positional[str | None] = Field(
        None,
        description=(
            "Optional repo-name pattern: a case-insensitive glob over the whole name by "
            "default; with / it matches owner/name, otherwise the name alone."
        ),
    )
    scope: Scope
    regex: bool = Field(
        False,
        description=(
            "Treat PATTERN as a case-insensitive, unanchored regex substring instead of a "
            "whole-target glob."
        ),
    )
    archived: ArchivedMode = Field("exclude", description=ARCHIVED)
    fork: ForkMode = Field("include", description=FORK)
    limit: Limit
    output: Output[Repo]

    def run(self) -> Iterator[Repo]:
        orgs = self.orgs(self.scope, scoped=bool(self.scope.team))
        teams = team_scopes(self.scope.team, orgs=orgs)
        if not orgs and not teams:
            raise UsageError("repos list requires --org or --team, or a github.default_org setting")
        self._check_pattern()
        filters = RepoListFilters(
            pattern=self.pattern, regex=self.regex, archived=self.archived, fork=FORKS[self.fork]
        )
        self.output.empty("No repositories found. Broaden your pattern or scope filters.")
        with self.client() as client, self.ctx.diag.progress("Listing repositories"):
            repos = list(ListRepos(client)(filters, orgs=orgs, team_scopes=teams))
        limit = self.limit.value
        rows = repos[:limit]
        if len(rows) < len(repos):
            total = plural(len(repos), "repository", "repositories")
            self.ctx.diag.info(f"showing {len(rows)} of {total}; omit --limit to list all")
        yield from rows

    def _check_pattern(self) -> None:
        if self.regex and not self.pattern:
            raise UsageError("--regex requires PATTERN")
        if self.regex and self.pattern:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise UsageError(f"invalid regular expression: {exc}") from exc


class ReposGroup(Group):
    """List GitHub repository inventory from org/team scopes."""

    commands = (List,)
