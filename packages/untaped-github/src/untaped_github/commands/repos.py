"""``untaped github repos``: the repository inventory of org and team scopes, and selectors."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Annotated

from pydantic import Field

from untaped.sdk import Group, Limit, Output, Positional, Short, UsageError, plural
from untaped_github.application import ListRepos, RepoListFilters
from untaped_github.commands._github import (
    ARCHIVED,
    FORK,
    FORKS,
    REPO,
    ForkMode,
    GithubCommand,
    Scope,
    team_scopes,
)
from untaped_github.domain import ArchivedMode
from untaped_github.domain.repo_id import split_repo_id
from untaped_github.records import Repo, RepoSelector


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


class Selector(GithubCommand):
    """Print a selector naming orgs, teams and repos, for a saved source to keep.

    A command that saves a selection of repos reads it on its stdin and asks
    github for the repos each time it uses it, so the selection follows the
    orgs and teams. Nothing is listed now: no token is needed, and the
    names are checked only for their shape.

    Examples
    --------
    ```console
    $ untaped github repos selector --team acme/platform --repo acme/tools --format pipe
    ```
    """

    org: list[str] = Field(
        default_factory=list, description="Every repository of this org. Repeatable."
    )
    team: list[str] = Field(
        default_factory=list,
        description="Every repository of this team: ORG/SLUG, or SLUG with exactly one --org. "
        "Repeatable.",
    )
    repo: Annotated[list[str], Short("-r")] = Field(default_factory=list, description=REPO)
    output: Output[RepoSelector]

    def run(self) -> RepoSelector:
        orgs = tuple(self.org)
        if not (orgs or self.team or self.repo):
            raise UsageError("repos selector needs --org, --team or --repo")
        for org in orgs:
            if not org or "/" in org or any(char.isspace() for char in org):
                raise UsageError(f"--org must be an org name, not {org!r}")
        teams = team_scopes(self.team, orgs=orgs)
        for name in self.repo:
            if split_repo_id(name) is None or any(char.isspace() for char in name):
                raise UsageError(f"--repo must be OWNER/NAME, not {name!r}")
        return RepoSelector(
            orgs=list(orgs),
            teams=[f"{team.org}/{team.slug}" for team in teams],
            repos=list(self.repo),
        )


class ReposGroup(Group):
    """List GitHub repository inventory from org/team scopes, or print a selector of them."""

    commands = (List, Selector)
