"""Cyclopts sub-app: ``untaped github repos``."""

from __future__ import annotations

import re
from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.github.application.scopes import TeamScope
from untaped.capabilities.github.cli._client import open_client
from untaped.capabilities.github.cli.scopes import (
    ArchivedOption,
    OrgOption,
    TeamOption,
    org_scope,
    parse_team_scopes,
)
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    LimitOption,
    UsageError,
    create_app,
    emit,
    plural,
    report_errors,
)

PatternArgument = Annotated[
    str | None,
    Parameter(
        help=(
            "Optional repo-name pattern. Glob by default; with / matches full_name, "
            "otherwise matches name."
        )
    ),
]
app = create_app(name="repos", help="List GitHub repository inventory from org/team scopes.")
# The table shows these; structured formats and -c/--columns reach every field.
_TABLE_COLUMNS = ["full_name", "default_branch", "private", "archived", "fork", "url"]


def _validate_args(
    pattern: str | None,
    *,
    regex: bool,
    orgs: tuple[str, ...],
    team_scopes: tuple[TeamScope, ...],
) -> None:
    if not orgs and not team_scopes:
        raise UsageError("repos list requires --org or --team, or a github.default_org setting")
    if regex and not pattern:
        raise UsageError("--regex requires PATTERN")
    if regex and pattern:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise UsageError(f"invalid regular expression: {exc}") from exc


@app.command(name="list")
def list_command(
    pattern: PatternArgument = None,
    /,
    *,
    org: OrgOption = None,
    team: TeamOption = None,
    regex: Annotated[
        bool,
        Parameter(
            name="--regex",
            negative="",
            help=(
                "Treat PATTERN as a case-insensitive, unanchored regex "
                "substring instead of a whole-target glob."
            ),
        ),
    ] = False,
    archived: ArchivedOption = "exclude",
    fork: Annotated[
        bool | None,
        Parameter(name="--fork", negative="--no-fork", help="Only forks; --no-fork excludes them."),
    ] = None,
    limit: LimitOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List repositories from additive org/team inventory scopes."""
    from untaped.capabilities.github.application import ListRepos, RepoListFilters  # noqa: PLC0415

    with report_errors():
        orgs = org_scope(org, scoped=bool(team))
        team_scopes = parse_team_scopes(team, orgs=orgs)
        _validate_args(pattern, regex=regex, orgs=orgs, team_scopes=team_scopes)
        filters = RepoListFilters(pattern=pattern, regex=regex, archived=archived, fork=fork)
        with open_client() as (client, ui), ui.progress("Listing repositories…"):
            repos = list(ListRepos(client)(filters, orgs=orgs, team_scopes=team_scopes))
            rows = [repo.model_dump(mode="json") for repo in repos[:limit]]
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            table_columns=_TABLE_COLUMNS,
            kind="github.repo",
            empty="No repositories found. Broaden your pattern or scope filters.",
        )
        if len(rows) < len(repos):
            total = plural(len(repos), "repository", "repositories")
            ui.message("info", f"showing {len(rows)} of {total}; omit --limit to list all")
