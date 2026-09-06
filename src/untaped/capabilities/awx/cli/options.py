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

OrganizationLookupOption = Annotated[
    str | None,
    Parameter(
        name=["--organization", "--org"],
        help="Scope name lookup to organization.",
    ),
]

OrganizationStdinLookupOption = Annotated[
    str | None,
    Parameter(
        name=["--organization", "--org"],
        help="Scope --stdin name lookups to an organization.",
    ),
]

InventoryOption = Annotated[
    str | None,
    Parameter(name="--inventory", help="Scope to inventory (Host/Group only)."),
]

InventoryLookupOption = Annotated[
    str | None,
    Parameter(
        name="--inventory",
        help=(
            "Scope name lookup to inventory (Host/Group only). Without this, "
            "name lookup is global and ambiguous if the same name exists "
            "across inventories."
        ),
    ),
]

InventoryStdinLookupOption = Annotated[
    str | None,
    Parameter(
        name="--inventory",
        help="Scope --stdin name lookups to an inventory (Host/Group only).",
    ),
]

InventoryOrganizationOption = Annotated[
    str | None,
    Parameter(
        name=["--inventory-organization", "--inventory-org"],
        help="Disambiguate same-named inventories across orgs (Host/Group only).",
    ),
]


__all__ = [
    "ByIdOption",
    "InventoryLookupOption",
    "InventoryOption",
    "InventoryOrganizationOption",
    "InventoryStdinLookupOption",
    "OrganizationLookupOption",
    "OrganizationOption",
    "OrganizationStdinLookupOption",
    "resolve_max_depth",
]
