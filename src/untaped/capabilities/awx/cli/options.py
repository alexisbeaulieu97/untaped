"""Shared Cyclopts options for AWX CLI command builders."""

from typing import Annotated

from cyclopts import Parameter

from untaped.api import raise_usage


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
        name="--filter", consume_multiple=False, help="Server filter KEY=VALUE (repeatable)."
    ),
]
StdinOption = Annotated[
    bool,
    Parameter(name="--stdin", negative="", help="Read names, IDs, or typed records from stdin."),
]
AllOption = Annotated[
    bool, Parameter(name="--all", negative="", help="Explicitly select every resource in scope.")
]
ParentOption = Annotated[
    str | None, Parameter(name="--parent", help="Scope to the parent resource name.")
]
YesOption = Annotated[bool, Parameter(name=["--yes", "-y"], negative="", help="Skip confirmation.")]
DryRunOption = Annotated[
    bool, Parameter(name="--dry-run", negative="", help="Preview without writing.")
]
ContinueOption = Annotated[
    bool,
    Parameter(
        name="--continue-on-error", negative="", help="Continue scheduling after a runtime failure."
    ),
]
ParallelOption = Annotated[
    int, Parameter(name=["--parallel", "-j"], help="Concurrent writes (default 1, capped at 10).")
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
    "OrganizationOption",
    "ParallelOption",
    "ParentOption",
    "SearchOption",
    "StdinOption",
    "UnverifiedOption",
    "YesOption",
    "resolve_max_depth",
]
