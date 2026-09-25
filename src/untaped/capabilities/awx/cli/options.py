"""Shared Cyclopts options for AWX CLI command builders."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capability_api import (
    DryRunOption,
    StdinOption,
    YesOption,
    raise_usage,
)
from untaped.capability_api import ParallelOption as _CoreParallelOption


def resolve_max_depth(depth: int | None, recursive: bool) -> int | None:
    """Translate the shared ``--depth`` / ``--recursive`` flag pair.

    ``--depth N`` wins when set (``N > 0`` implies recursion);
    ``--recursive`` alone is unlimited (``None``); neither caps the
    traversal at ``0``. Used by every recursive inspector (``nodes``,
    ``usage``) so the flag semantics can't drift between commands.
    """
    if depth is not None and depth < 0:
        raise_usage("--depth must be non-negative")
    if depth is not None:
        return depth
    return None if recursive else 0


NamesArgument = Annotated[
    list[str] | None,
    Parameter(help="Names of the resources to select, or AWX ids with --by-id."),
]
"""Positional resource names; declare it positional-only (before ``/``)."""

ByIdOption = Annotated[
    bool,
    Parameter(
        name="--by-id",
        negative="",
        help="Look up identifiers as AWX numeric ids instead of names.",
    ),
]

OrganizationOption = Annotated[
    str | None,
    Parameter(name=["--organization", "--org"], help="Scope to organization."),
]

InventoryOption = Annotated[
    str | None,
    Parameter(
        name="--inventory", help="Scope Host, Group, or InventorySource selection to an inventory."
    ),
]

InventoryOrganizationOption = Annotated[
    str | None,
    Parameter(
        name=["--inventory-organization", "--inventory-org"],
        help="Scope Host, Group, or InventorySource selection by inventory organization.",
    ),
]


# Shared controls deliberately use the same names on every configuration command.
SearchOption = Annotated[str | None, Parameter(name="--search", help="Server-side search.")]
FilterOption = Annotated[
    list[str] | None,
    Parameter(
        name="--filter",
        consume_multiple=False,
        negative="",
        help="Server-side filter KEY=VALUE (repeatable).",
    ),
]
AllOption = Annotated[
    bool, Parameter(name="--all", negative="", help="Explicitly select every resource in scope.")
]
ParentOption = Annotated[
    str | None, Parameter(name="--parent", help="Scope to the parent resource name.")
]
ContinueOption = Annotated[
    bool,
    Parameter(
        name="--continue-on-error", negative="", help="Continue scheduling after a runtime failure."
    ),
]
ParallelOption = Annotated[
    _CoreParallelOption,
    Parameter(help="Maximum number of concurrent writes (capped at 10)."),
]
WaitTimeoutOption = Annotated[
    float | None,
    Parameter(
        name="--timeout",
        help=(
            "Stop waiting after this many seconds per execution (needs --wait or --track); "
            "unfinished executions fail the row and keep running."
        ),
    ),
]
UnverifiedOption = Annotated[
    bool,
    Parameter(
        name="--allow-unverified",
        negative="",
        help="Allow unverified configuration writes; requires --yes.",
    ),
]


__all__ = [
    "AllOption",
    "ByIdOption",
    "ContinueOption",
    "DryRunOption",
    "FilterOption",
    "InventoryOption",
    "InventoryOrganizationOption",
    "NamesArgument",
    "OrganizationOption",
    "ParallelOption",
    "ParentOption",
    "SearchOption",
    "StdinOption",
    "UnverifiedOption",
    "WaitTimeoutOption",
    "YesOption",
    "resolve_max_depth",
]


WITH_SCM_HELP = (
    "Add scm_url, effective_scm_ref (the template's scm_branch when set and its "
    "project allows the override, else the project's) and project_allow_override."
)


def offers_with_scm(spec: ResourceSpec) -> bool:
    """``--with-scm`` applies to kinds that run a project's playbook."""
    return any(ref.field == "project" and ref.kind == "Project" for ref in spec.fk_refs)
