"""Copy one resource through AWX's ``<kind>/<id>/copy/`` endpoint.

AWX copies server-side, carrying what the copy endpoint can reproduce
(settings, relationships, survey). Planning is read-only: it refuses a name
already used in the source's scope and a source AWX will not let the caller
copy, and it lists the parts AWX reports it will leave behind
(``can_copy_without_user_input: false``) so the preview can show them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.application.resource_names import name_taken
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.domain import CopyOutcome, ResourceSpec
from untaped.capabilities.awx.errors import (
    BadRequestError,
    ConflictError,
    PermissionDeniedError,
)

_UNABLE_SUFFIX = "_unable_to_copy"


@dataclass(frozen=True)
class CopyPlan:
    """A validated copy of ``source`` to ``name``; ``not_carried`` names the gaps."""

    source: SelectedResource
    name: str
    not_carried: tuple[str, ...]


class CopyResource:
    """Plan and execute a single server-side copy."""

    def __init__(self, client: ResourceClient) -> None:
        self._client = client

    def plan(self, spec: ResourceSpec, source: SelectedResource, name: str) -> CopyPlan:
        """Refuse before any write when the copy cannot or must not happen."""
        if not name.strip():
            raise BadRequestError("the copy name must not be empty")
        if name == source.name:
            raise ConflictError(f"{spec.kind} {name!r} is the source; choose a different name")
        if name_taken(self._client, spec, source.record, name):
            raise ConflictError(f"{spec.kind} {name!r} already exists in the source's scope")
        check = self._client.sub_endpoint_request(spec, source.id, "copy", "GET")
        if not check.get("can_copy"):
            raise PermissionDeniedError(
                f"AWX does not allow copying {spec.kind} {source.name!r} (can_copy: false)"
            )
        return CopyPlan(source=source, name=name, not_carried=_not_carried(check))

    def __call__(self, spec: ResourceSpec, plan: CopyPlan) -> CopyOutcome:
        """POST the copy and report the new resource's id."""
        created = self._client.sub_endpoint_request(
            spec, plan.source.id, "copy", "POST", json={"name": plan.name}
        )
        new_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(new_id, int) or isinstance(new_id, bool):
            raise BadRequestError(f"{spec.kind} copy returned no integer id")
        return CopyOutcome(
            id=new_id,
            name=str(created.get("name") or plan.name),
            source_id=plan.source.id,
            kind=spec.kind,
            action="created",
            not_carried=list(plan.not_carried),
        )


def _not_carried(check: dict[str, Any]) -> tuple[str, ...]:
    """Name what AWX says it will not copy without user input."""
    if check.get("can_copy_without_user_input", True):
        return ()
    parts: list[str] = []
    for key, value in check.items():
        if key.endswith(_UNABLE_SUFFIX) and value:
            label = key.removesuffix(_UNABLE_SUFFIX)
            items = value if isinstance(value, list) else [value]
            parts.append(f"{label}: {', '.join(str(item) for item in items)}")
    return tuple(parts) or ("some related resources (AWX gave no details)",)


__all__ = ["CopyPlan", "CopyResource"]
