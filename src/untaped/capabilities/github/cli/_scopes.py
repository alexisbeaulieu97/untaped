"""Shared CLI scope option aliases and parsers."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.api import ConfigError
from untaped.capabilities.github.application.scopes import TeamScope, normalize_team_scopes

OrgOption = Annotated[
    list[str] | None,
    Parameter(name="--org", help="GitHub org scope. Repeatable.", consume_multiple=False),
]
TeamOption = Annotated[
    list[str] | None,
    Parameter(
        name="--team",
        help="Team ORG/SLUG, or SLUG with exactly one --org. Repeatable.",
        consume_multiple=False,
    ),
]


def parse_team_scopes(
    values: list[str] | None, *, orgs: tuple[str, ...] = ()
) -> tuple[TeamScope, ...]:
    """Parse repeatable ``--team`` values into explicit org/slug scopes."""
    try:
        return normalize_team_scopes(values, orgs=orgs)
    except ValueError as exc:
        raise ConfigError("--team must be ORG/SLUG unless exactly one --org is provided") from exc
