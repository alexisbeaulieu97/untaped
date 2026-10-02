"""Shared application scope value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeamScope:
    """A GitHub team scoped by owning organization."""

    org: str
    slug: str


def normalize_team_scopes(
    values: list[str] | tuple[str, ...] | None, *, orgs: tuple[str, ...] = ()
) -> tuple[TeamScope, ...]:
    """Parse repeatable team values into explicit organization/team scopes."""
    scopes: list[TeamScope] = []
    for value in values or ():
        parts = value.split("/")
        if len(parts) == 2 and all(parts):
            org, slug = parts
        elif "/" not in value and value and len(orgs) == 1:
            org = orgs[0]
            slug = value
        else:
            raise ValueError("team must be ORG/SLUG unless exactly one org is provided")
        scopes.append(TeamScope(org=org, slug=slug))
    return tuple(scopes)
