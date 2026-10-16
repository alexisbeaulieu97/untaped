"""The controller loader: every resource the token can read, as an ``AwxResources`` snapshot.

Two passes. The first lists each catalog kind once and seeds the resolver
with the rows, so the second, which turns each row into the document
``export`` would write, names its references from those rows instead of a
read per reference. Every kind is read, ``export``'s read-only ones too: a
rule may judge what ``apply`` cannot write.

A foreign key resolves by the id the controller holds
(:func:`~untaped_awx.application.lint.resolution.id_references`); a
schedule's parent and a workflow node's references, which export names with
their organization, resolve by name among the items.

A kind the token may not list is left out: its references are
``unresolved``, not ``absent``. Its names come from the rows'
``summary_fields`` where AWX embeds them; a record the token can neither
list nor read is named ``#<id>``, and references to it are ``unresolved``.
Each document's secret-named variables are redacted before it joins the
snapshot, since the snapshot is cached on disk.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from untaped.sdk import NO_PROGRESS, ProgressHandle
from untaped_awx.application.lint.resolution import (
    OfflineBase,
    SnapshotReferences,
    id_references,
    opaque_references,
)
from untaped_awx.application.lint.snapshot import kind_facts
from untaped_awx.application.resources.mutation_refs import PlannedId
from untaped_awx.application.resources.ports import (
    Catalog,
    FkResolver,
    ProjectPlaybooks,
    ResourceClient,
    WorkflowNodeRepository,
)
from untaped_awx.application.resources.save_resource import ResourceSnapshot, SaveResource
from untaped_awx.domain import IdentityRef, Resource, ResourceSpec
from untaped_awx.domain.lint.resources import AwxResources, ControllerFacts, Linted
from untaped_awx.domain.lint.secrets import redact_variables
from untaped_awx.errors import AwxApiError, PermissionDeniedError
from untaped_lint.api import ResourceLocation

type PathOf = Callable[[ResourceSpec, int], str]
"""A record's API path: ``job_templates/14`` (the CLI wires it; application reads no path)."""


def load_controller(
    *,
    client: ResourceClient,
    fk: FkResolver,
    catalog: Catalog,
    nodes: WorkflowNodeRepository,
    playbooks: ProjectPlaybooks,
    path_of: PathOf,
    progress: ProgressHandle = NO_PROGRESS,
) -> AwxResources:
    """Read every catalog kind from the controller into one snapshot."""
    kinds = catalog.kinds()
    listed: list[tuple[ResourceSpec, list[dict[str, Any]]]] = []
    for done, kind in enumerate(kinds):
        progress.update(kind, phase="listing", done=done, total=len(kinds))
        spec = catalog.get(kind)
        try:
            rows = list(client.list(spec))
        except PermissionDeniedError:
            continue
        fk.seed(kind, rows)
        listed.append((spec, rows))
    loaded = tuple(spec.kind for spec, _rows in listed)
    for kind, summaries in _summaries(listed, unlisted=set(kinds) - set(loaded)).items():
        fk.seed(kind, summaries)
    readable = _Readable(fk)
    save = SaveResource(client, readable, nodes=nodes)
    total = sum(len(rows) for _spec, rows in listed)
    read: list[tuple[ResourceSpec, ResourceSnapshot]] = []
    for spec, rows in listed:
        for row in rows:
            progress.update(
                f"{spec.kind} {row.get('name')}", phase="reading", done=len(read), total=total
            )
            read.append((spec, save.snapshot_from_record(spec, row)))
    progress.update(None, done=total, total=total)
    resources = [redact_variables(snapshot.resource) for _spec, snapshot in read]
    identities = {
        (spec.kind, int(snapshot.record["id"])): _identity(resource)
        for (spec, snapshot), resource in zip(read, resources, strict=True)
    }
    base = OfflineBase(loaded, hidden=readable.hidden)
    references = SnapshotReferences(resources, catalog=catalog, base=base)
    items = tuple(
        Linted(
            origin="controller",
            location=ResourceLocation(id=path_of(spec, int(snapshot.record["id"]))),
            resource=resource,
            references=(
                *id_references(spec, resource, snapshot.fk_ids, listed=identities),
                *references.of(resource, skip=_keys(spec)),
                *opaque_references(snapshot.opaque_nodes),
            ),
            controller=_facts(spec, snapshot.record, playbooks),
        )
        for (spec, snapshot), resource in zip(read, resources, strict=True)
    )
    return AwxResources(items=items, kinds=kind_facts(catalog), loaded=loaded, online=True)


def _keys(spec: ResourceSpec) -> frozenset[str]:
    """The foreign keys :func:`id_references` resolves: every one but a polymorphic parent."""
    return frozenset(ref.field for ref in spec.fk_refs if not ref.polymorphic)


def _identity(resource: Resource) -> IdentityRef:
    """The identity a live item is found by: its kind, name, organization and parent."""
    metadata = resource.metadata
    return IdentityRef(
        kind=resource.kind,
        name=metadata.name,
        organization=metadata.organization,
        parent=metadata.parent,
    )


def _summaries(
    listed: Iterable[tuple[ResourceSpec, list[dict[str, Any]]]], *, unlisted: set[str]
) -> dict[str, list[dict[str, Any]]]:
    """The ``{id, name}`` rows' ``summary_fields`` embed for each kind the token cannot list."""
    found: dict[str, dict[int, dict[str, Any]]] = {}
    for spec, rows in listed:
        keys = [ref for ref in spec.fk_refs if ref.kind in unlisted and not ref.polymorphic]
        for row in rows:
            summaries = row.get("summary_fields")
            if not keys or not isinstance(summaries, Mapping):
                continue
            for ref in keys:
                value = summaries.get(ref.field)
                for each in value if isinstance(value, list) else [value]:
                    if isinstance(each, Mapping) and _named(each):
                        found.setdefault(str(ref.kind), {}).setdefault(each["id"], dict(each))
    return {kind: list(rows.values()) for kind, rows in found.items()}


def _named(value: Mapping[str, Any]) -> bool:
    return (
        isinstance(value.get("id"), int)
        and not isinstance(value.get("id"), bool)
        and isinstance(value.get("name"), str)
    )


class _Readable:
    """The resolver export names references through, a record the token cannot read kept.

    AWX refuses a record's detail to a token that may not read it (403); export
    would fail on the whole run. Here it names that record ``#<id>`` and
    remembers it in :attr:`hidden`, so a reference to it is ``unresolved``.
    """

    def __init__(self, fk: FkResolver) -> None:
        self._fk = fk
        self.hidden: set[tuple[str, str]] = set()

    def _unread(self, kind: str, id_: int) -> str:
        name = f"#{id_}"
        self.hidden.add((kind, name))
        return name

    def id_to_name(self, kind: str, id_: int) -> str:
        try:
            return self._fk.id_to_name(kind, id_)
        except PermissionDeniedError:
            return self._unread(kind, id_)

    def id_to_identity(self, kind: str, id_: int) -> IdentityRef:
        try:
            return self._fk.id_to_identity(kind, id_)
        except PermissionDeniedError:
            return IdentityRef(kind=kind, name=self._unread(kind, id_))

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> PlannedId:
        return self._fk.name_to_id(kind, name, scope=scope)

    def validate_id(self, kind: str, id_: int, *, scope: dict[str, str] | None = None) -> int:
        return self._fk.validate_id(kind, id_, scope=scope)

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, PlannedId]:
        return self._fk.resolve_polymorphic(value)

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        self._fk.prefetch(plan)

    def seed(self, kind: str, records: Iterable[Mapping[str, Any]]) -> None:
        self._fk.seed(kind, records)


def _facts(
    spec: ResourceSpec, record: dict[str, Any], playbooks: ProjectPlaybooks
) -> ControllerFacts:
    status = record.get("status")
    failed = record.get("last_job_failed")
    hosts = record.get("total_hosts")
    found: tuple[str, ...] | None = None
    if spec.kind == "Project" and status == "successful":  # what playbook-missing reads
        try:
            found = playbooks.playbooks(int(record["id"]))
        except AwxApiError:
            found = None
    return ControllerFacts(
        status=status if isinstance(status, str) else None,
        last_job_failed=failed if isinstance(failed, bool) else None,
        total_hosts=hosts if spec.kind == "Inventory" and _count(hosts) else None,
        playbooks=found,
    )


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


__all__ = ["PathOf", "load_controller"]
