"""Identifier resolution shared by use cases.

``resolve_id`` turns a CLI identifier (either a decimal id or a name
plus optional scope) into a record id, raising :class:`ResourceNotFound`
when the name lookup misses. Centralises the ``isdecimal`` numeric
fast-path so new use cases don't reinvent it.
"""

from __future__ import annotations

from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec
from untaped_awx.errors import ResourceNotFound


def resolve_id(
    client: ResourceClient,
    spec: ResourceSpec,
    identifier: str,
    *,
    scope: dict[str, str] | None = None,
) -> int:
    if identifier.isdecimal():
        return int(identifier)
    record = client.find_by_identity(spec, name=identifier, scope=scope)
    if record is None:
        raise ResourceNotFound(spec.kind, {"name": identifier, **(scope or {})})
    return record.id
