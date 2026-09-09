"""AWX batch editor document contract: immutable identity and bounded editable fields."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from uuid import uuid4

import yaml

from untaped.capabilities.awx.application.apply_membership import MembershipSnapshots
from untaped.capabilities.awx.application.save_resource import ResourceSnapshot, SaveResource
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.domain import Resource, ResourceSpec
from untaped.capability_api import ConfigError


class EditResources:
    """Keep the original selection and projection separate from untrusted edits."""

    def __init__(
        self,
        spec: ResourceSpec,
        selected: Sequence[SelectedResource],
        saver: SaveResource,
        *,
        fields: Sequence[str] | None = None,
    ) -> None:
        immutable = {"id", "name", "organization", "parent", "kind", "type", "unified_job_template"}
        if spec.apply_strategy == "inventory_child":
            immutable.add("inventory")
        writable = (
            (set(spec.canonical_fields) | {ref.field for ref in spec.fk_refs})
            - immutable
            - set(spec.read_only_fields)
        )
        if fields is not None and not set(fields) <= writable:
            raise ConfigError("--field must name editable fields; identity fields cannot be edited")
        allowed = set(fields) if fields is not None else writable
        self._allowed: dict[int, set[str]] = {}
        self._spec = spec
        self._selected = {item.id: copy.deepcopy(item) for item in selected}
        self._snapshots: dict[int, ResourceSnapshot] = {}
        self._memberships: dict[tuple[str, int, str], tuple[dict[str, Any], ...]] = {}
        self._identities: dict[int, dict[str, Any]] = {}
        self._documents: list[dict[str, Any]] = []
        for item in selected:
            snapshot = copy.deepcopy(saver.snapshot_from_record(spec, item.record))
            self._selected[item.id] = replace(self._selected[item.id], record=snapshot.record)
            self._allowed[item.id] = allowed - set(snapshot.read_only_fields)
            if fields is not None and not set(fields) <= self._allowed[item.id]:
                raise ConfigError("--field must name editable fields for every selected resource")
            resource = snapshot.resource
            self._snapshots[item.id] = snapshot
            self._memberships.update(
                {(spec.kind, item.id, field): rows for field, rows in snapshot.memberships.items()}
            )
            identity = {
                "token": uuid4().hex,
                "kind": spec.kind,
                "id": item.id,
                "metadata": resource.metadata.model_dump(exclude_none=True),
                "scope": dict(item.scope),
            }
            self._identities[item.id] = identity
            self._documents.append(
                {
                    "identity": identity,
                    "spec": {
                        key: value
                        for key, value in resource.spec.items()
                        if key in self._allowed[item.id]
                    },
                }
            )

    @property
    def membership_snapshots(self) -> MembershipSnapshots:
        """Return an isolated copy of the pre-editor relationship baseline."""
        return copy.deepcopy(self._memberships)

    def render(self) -> str:
        """Render one YAML batch with explicit read-only identity guidance."""
        return (
            "# Edit spec only. Identity is read-only; remove a document to deselect it.\n"
            "# Missing top-level fields stay unchanged; supplied nested values replace.\n"
            + yaml.safe_dump_all(self._documents, sort_keys=False)
        )

    def parse(self, text: str) -> tuple[list[Resource], tuple[SelectedResource, ...]]:
        """Validate every document before returning resources bound to original IDs.

        Diagnostics never include parser excerpts or values, which may contain
        freshly entered secrets even when the YAML cannot be parsed.
        """
        try:
            documents = list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            raise ConfigError("invalid editor YAML; fix its syntax and reopen") from exc
        resources: list[Resource] = []
        selected: list[SelectedResource] = []
        seen: set[int] = set()
        for document in documents:
            if document is None:
                continue
            if not isinstance(document, dict) or set(document) != {"identity", "spec"}:
                raise ConfigError("each editor document must contain only identity and spec")
            identity = document["identity"]
            if not isinstance(identity, dict):
                raise ConfigError("editor identity must remain unchanged")
            id_ = identity.get("id")
            if type(id_) is not int or id_ not in self._selected:
                raise ConfigError("editor cannot add or retarget resources")
            if id_ in seen:
                raise ConfigError("duplicate editor document")
            if identity != self._identities[id_]:
                raise ConfigError("editor identity, name, kind and scope must remain unchanged")
            body = document["spec"]
            if not isinstance(body, dict) or not set(body) <= self._allowed[id_]:
                raise ConfigError("editor spec contains fields outside the allowed edit boundary")
            seen.add(id_)
            resources.append(
                Resource(
                    kind=self._spec.kind,
                    metadata=self._snapshots[id_].resource.metadata.model_copy(deep=True),
                    spec=self._preserve_fk_ids(id_, body),
                )
            )
            selected.append(copy.deepcopy(self._selected[id_]))
        return resources, tuple(selected)

    def _preserve_fk_ids(self, id_: int, body: dict[str, Any]) -> dict[str, Any]:
        """Bind unchanged labels to the IDs from their original display reads.

        Changed strings/mappings/integers remain untouched for the shared FK
        resolver. Matching list occurrences are consumed in snapshot order.
        """
        snapshot = self._snapshots[id_]
        result = copy.deepcopy(body)
        for ref in self._spec.fk_refs:
            if ref.field not in body or ref.field not in snapshot.fk_ids:
                continue
            before = snapshot.resource.spec.get(ref.field)
            ids = snapshot.fk_ids[ref.field]
            after = body[ref.field]
            if (
                ref.multi
                and isinstance(before, list)
                and isinstance(ids, list)
                and isinstance(after, list)
            ):
                remaining = list(zip(before, ids, strict=True))
                mapped = []
                for value in after:
                    match = next(
                        (i for i, (label, _) in enumerate(remaining) if label == value), None
                    )
                    mapped.append(remaining.pop(match)[1] if match is not None else value)
                result[ref.field] = mapped
            elif before is not None and before == after:
                result[ref.field] = ids
        return result
