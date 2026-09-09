"""Use case: bulk-save AWX resources as portable envelopes."""

from __future__ import annotations

import re
from collections.abc import Iterator

from untaped.capabilities.awx.application.ports import Catalog, FkResolver, ResourceClient
from untaped.capabilities.awx.application.save_resource import SaveResource
from untaped.capabilities.awx.domain import IdentityRef, Metadata, ResourceSpec, SaveOutcome
from untaped.capabilities.awx.errors import AwxApiError

_UNSAFE_FILENAME_CHARS = re.compile(r"[/\\\x00-\x1f]")


class SaveResources:
    def __init__(self, client: ResourceClient, fk: FkResolver, catalog: Catalog) -> None:
        self._save_one = SaveResource(client, fk)
        self._catalog = catalog

    def __call__(
        self,
        *,
        all_kinds: bool = False,
        kind: str | None = None,
        filters: dict[str, str] | None = None,
        organization: str | None = None,
    ) -> Iterator[SaveOutcome]:
        specs = self._target_specs(all_kinds=all_kinds, kind=kind)
        return self._save_specs(specs, filters=filters or {}, organization=organization)

    def _save_specs(
        self,
        specs: list[ResourceSpec],
        *,
        filters: dict[str, str],
        organization: str | None,
    ) -> Iterator[SaveOutcome]:
        for spec in specs:
            if spec.fidelity == "read_only":
                yield SaveOutcome(
                    kind=spec.kind,
                    action="skipped",
                    detail="not roundtrippable in v0",
                )
                continue
            server_filters = _server_filters_for_spec(
                spec, filters=filters, organization=organization
            )
            incompatible = _filter_field_not_on_spec(server_filters, spec)
            if incompatible is not None:
                yield SaveOutcome(
                    kind=spec.kind,
                    action="skipped",
                    detail=f"filter field {incompatible!r} not on this kind",
                )
                continue
            records = self._save_one.find_all(spec, params=server_filters or None)
            for record in records:
                resource = self._save_one.from_record(spec, record)
                if organization is not None and not _metadata_matches_org(
                    resource.metadata, organization
                ):
                    continue
                yield (
                    SaveOutcome(
                        kind=spec.kind,
                        name=resource.metadata.name,
                        action="saved",
                        resource=resource,
                        filename=resource_filename(spec.kind, resource.metadata),
                        header_comment=(spec.fidelity_note if spec.fidelity != "full" else None),
                    )
                )

    def _target_specs(self, *, all_kinds: bool, kind: str | None) -> list[ResourceSpec]:
        if all_kinds:
            return [self._catalog.get(kind_name) for kind_name in self._catalog.kinds()]
        if kind is None:
            raise AwxApiError("pass --all-kinds or --kind")
        try:
            return [self._catalog.by_cli_name(kind)]
        except AwxApiError:
            return [self._catalog.get(kind)]


def _filter_field_not_on_spec(filters: dict[str, str], spec: ResourceSpec) -> str | None:
    """Return a filter field that is not represented in the domain spec."""
    fields = spec.known_fields
    for key in filters:
        base = key.split("__", 1)[0]
        if base not in fields:
            return base
    return None


def _server_filters_for_spec(
    spec: ResourceSpec,
    *,
    filters: dict[str, str],
    organization: str | None,
) -> dict[str, str]:
    server_filters = dict(filters)
    if organization is None:
        return server_filters
    org_filter = _organization_filter_for_spec(spec, organization)
    return {**server_filters, **org_filter}


def _organization_filter_for_spec(spec: ResourceSpec, organization: str) -> dict[str, str]:
    """Return the safest server-side org filter for a bulk-save kind."""
    if spec.kind == "Schedule":
        return {}
    fields = spec.known_fields
    if "organization" in fields:
        return {"organization__name": organization}
    if "inventory" in fields:
        return {"inventory__organization__name": organization}
    return {}


def _metadata_matches_org(metadata: Metadata, organization: str) -> bool:
    if metadata.organization == organization:
        return True
    parent = metadata.parent
    while parent is not None:
        if parent.organization == organization:
            return True
        parent = parent.parent
    return False


def _safe_filename_segment(name: str) -> str:
    """Return a filesystem-safe segment derived from an AWX resource name."""
    if not name:
        return "unnamed"
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", name).strip(". ")
    return cleaned or "unnamed"


def resource_filename(kind: str, metadata: Metadata) -> str:
    """Encode the identity tuple in the filename so same-named records do not collide."""
    parts: list[str] = [kind]
    if metadata.parent is not None:
        parent: IdentityRef | None = metadata.parent
        while parent is not None:
            parts.append(parent.kind)
            if parent.organization:
                parts.append(parent.organization)
            parts.append(parent.name)
            parent = parent.parent
    elif metadata.organization is not None:
        parts.append(metadata.organization)
    parts.append(metadata.name)
    return "__".join(_safe_filename_segment(p) for p in parts) + ".yml"
