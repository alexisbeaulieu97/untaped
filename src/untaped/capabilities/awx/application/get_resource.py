"""Resolve CLI resource identifiers (names with scope, or explicit ``--by-id``) to IDs."""

from __future__ import annotations

from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.errors import ResourceNotFoundError
from untaped.capability_api import ConfigError


def parse_resource_id(identifier: str) -> int:
    """Parse a CLI resource id for explicit ``--by-id`` modes."""
    if not identifier.isdecimal():
        raise ConfigError(f"not a numeric id: {identifier!r}")
    return int(identifier)


def resolve_identity(
    resources: ResourceClient,
    spec: ResourceSpec,
    identifier: str,
    *,
    scope: dict[str, str] | None = None,
    by_id: bool = False,
) -> int:
    """Resolve a CLI identifier to an AWX id.

    Names resolve via ``find_by_identity`` (honouring ``scope``); numeric
    ids are accepted only in explicit ``by_id`` mode, so a resource whose
    *name* is numeric never gets shadowed by an id lookup.
    """
    if by_id:
        return parse_resource_id(identifier)
    record = resources.find_by_identity(spec, name=identifier, scope=scope)
    if record is None:
        raise ResourceNotFoundError(spec.kind, {"name": identifier, **(scope or {})})
    return record.id
