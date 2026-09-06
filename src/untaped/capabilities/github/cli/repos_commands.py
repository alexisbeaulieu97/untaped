"""Cyclopts sub-app: ``untaped github repos``."""

from __future__ import annotations

import re
from typing import Annotated

from cyclopts import Parameter

from untaped.api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    create_app,
    emit,
    report_errors,
)
from untaped.capabilities.github.application.scopes import TeamScope
from untaped.capabilities.github.cli._client import open_client
from untaped.capabilities.github.cli._scopes import OrgOption, TeamOption, parse_team_scopes

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


def _validate_args(
    pattern: str | None,
    *,
    regex: bool,
    orgs: tuple[str, ...],
    team_scopes: tuple[TeamScope, ...],
) -> None:
    if not orgs and not team_scopes:
        raise ConfigError(
            "repos list requires --org or --team; user-owned repository inventory is not "
            "supported in v1"
        )
    if regex and not pattern:
        raise ConfigError("--regex requires PATTERN")
    if regex and pattern:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"invalid regular expression: {exc}") from exc


@app.command(name="list")
def list_command(
    pattern: PatternArgument = None,
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
    archived: Annotated[
        bool | None,
        Parameter(name="--archived", negative="--no-archived"),
    ] = None,
    fork: Annotated[bool | None, Parameter(name="--fork", negative="--no-fork")] = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List repositories from additive org/team inventory scopes."""
    from untaped.capabilities.github.application import ListRepos, RepoListFilters  # noqa: PLC0415

    with report_errors():
        orgs = tuple(org or ())
        team_scopes = parse_team_scopes(team, orgs=orgs)
        _validate_args(pattern, regex=regex, orgs=orgs, team_scopes=team_scopes)
        filters = RepoListFilters(pattern=pattern, regex=regex, archived=archived, fork=fork)
        with open_client() as (client, ui), ui.progress("Listing repositories…"):
            rows = [
                repo.model_dump()
                for repo in ListRepos(client)(
                    filters,
                    orgs=orgs,
                    team_scopes=team_scopes,
                )
            ]
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="github.repo",
            empty="No repositories found. Broaden your pattern or scope filters.",
        )
