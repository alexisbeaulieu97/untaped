"""Name availability within a resource's identity scope.

Copy and rename give a resource a new ``name`` inside the same scope as an
existing record (its organization, inventory, ...). A name is free only when
no record of the kind shares every non-name identity key with that record.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.errors import AmbiguousIdentityError


def name_scope_params(spec: ResourceSpec, record: Mapping[str, Any], name: str) -> dict[str, str]:
    """AWX list filters matching ``name`` in ``record``'s identity scope."""
    params = {"name": name}
    for key in spec.identity_keys:
        if key == "name":
            continue
        value = record.get(key)
        if value is None:
            params[f"{key}__isnull"] = "true"
        else:
            params[key] = str(value)
    return params


def name_taken(
    client: ResourceClient, spec: ResourceSpec, record: Mapping[str, Any], name: str
) -> bool:
    """Whether another record of ``spec`` already uses ``name`` in that scope."""
    try:
        return client.find(spec, params=name_scope_params(spec, record, name)) is not None
    except AmbiguousIdentityError:
        return True


__all__ = ["name_scope_params", "name_taken"]
