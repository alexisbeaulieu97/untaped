"""Shared CLI scope option aliases and parsers."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.github.application.inventory import RepositoryInventoryItem
from untaped.capabilities.github.application.scopes import TeamScope, normalize_team_scopes
from untaped.capability_api import ConfigError, UsageError, read_stdin_input

REPO_KINDS = frozenset({"github.repo", "github.repo_hit", "github.sweep_repo"})
"""Pipe record kinds whose ``full_name`` names a repository for ``--stdin``."""

OrgOption = Annotated[
    list[str] | None,
    Parameter(
        name="--org", help="GitHub org scope. Repeatable.", consume_multiple=False, negative=""
    ),
]
TeamOption = Annotated[
    list[str] | None,
    Parameter(
        name="--team",
        help="Team ORG/SLUG, or SLUG with exactly one --org. Repeatable.",
        consume_multiple=False,
        negative="",
    ),
]


def parse_team_scopes(
    values: list[str] | None, *, orgs: tuple[str, ...] = ()
) -> tuple[TeamScope, ...]:
    """Parse repeatable ``--team`` values into explicit org/slug scopes."""
    try:
        return normalize_team_scopes(values, orgs=orgs)
    except ValueError as exc:
        raise UsageError("--team must be ORG/SLUG unless exactly one --org is provided") from exc


def read_stdin_repos() -> tuple[tuple[str, ...], tuple[RepositoryInventoryItem, ...]]:
    """Read ``--stdin`` repos as names to look up plus records complete enough to use as-is.

    A piped record that carries ``default_branch`` and a clone or web URL
    (``repos list`` output) needs no API lookup; other records and bare
    ``owner/name`` lines return as names.
    """
    piped = read_stdin_input(accept_kinds=REPO_KINDS)
    if piped.records is None:
        return piped.values, ()
    names: list[str] = []
    items: list[RepositoryInventoryItem] = []
    for env in piped.records:
        record = env.record
        full_name = record.get("full_name")
        if not isinstance(full_name, str) or not full_name.strip():
            raise ConfigError(f"line {env.lineno}: record 'full_name' is missing or blank")
        if record.get("default_branch") and (record.get("clone_url") or record.get("html_url")):
            items.append(RepositoryInventoryItem.model_validate(record))
        else:
            names.append(full_name.strip())
    return tuple(names), tuple(items)
