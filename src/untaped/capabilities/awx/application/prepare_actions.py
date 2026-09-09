"""Validate a complete action selection and freeze inventory source expansion."""

from collections.abc import Sequence

from untaped.capabilities.awx.application.ports import Catalog, ResourceClient
from untaped.capabilities.awx.application.selection import (
    SelectedResource,
    SelectionRequest,
    SelectionResolver,
)
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.errors import AwxApiError


def prepare_action_targets(
    client: ResourceClient,
    catalog: Catalog,
    spec: ResourceSpec,
    selected: Sequence[SelectedResource],
    *,
    action: str,
) -> tuple[ResourceSpec, tuple[SelectedResource, ...]]:
    """Resolve every eligible source before any POST, never server-side aggregate sync."""
    if not selected:
        raise AwxApiError(f"No {spec.kind} targets selected for {action}")
    if action != "sync":
        return spec, tuple(selected)
    targets = tuple(selected)
    if spec.kind == "Inventory":
        source_spec = catalog.get("InventorySource")
        expanded: dict[int, SelectedResource] = {}
        for item in selected:
            label = f"Inventory {item.name!r} (id={item.id})"
            if item.record.get("kind", "") not in ("", "constructed"):
                raise AwxApiError(f"{label}: sync is unsupported for {item.record.get('kind')}")
            sources = SelectionResolver(client, catalog).resolve(
                source_spec,
                SelectionRequest(filters={"inventory": str(item.id)}, mutation=True),
            )
            if not sources:
                raise AwxApiError(f"{label}: no inventory sources to sync")
            expanded.update((source.id, source) for source in sources)
        spec, targets = source_spec, tuple(expanded.values())
    for item in targets:
        if spec.kind == "InventorySource" and item.record.get("source") in (None, "", "file"):
            raise AwxApiError(f"InventorySource {item.name!r} (id={item.id}): no syncable source")
        if spec.kind == "Project" and not item.record.get("scm_type"):
            raise AwxApiError(f"Project {item.name!r} (id={item.id}): manual project cannot sync")
    return spec, targets
