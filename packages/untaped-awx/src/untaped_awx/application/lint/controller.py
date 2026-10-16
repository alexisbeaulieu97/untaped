"""The controller loader: every resource the token can read, as an ``AwxResources`` snapshot.

Two passes. The first lists each catalog kind once and seeds the resolver
with the rows, so the second, which turns each row into the document
``export`` would write, names its references from those rows instead of a
read per reference. Every kind is read, ``export``'s read-only ones too: a
rule may judge what ``apply`` cannot write.

Every reference resolves by the id the controller holds
(:func:`~untaped_awx.application.lint.resolution.id_references`), to the
location of the item it names: foreign keys, a schedule's parent, and what
a workflow node runs and prompts with (export's node graph keeps the ids).

A kind the token may not list is left out: its references are
``unresolved``, not ``absent``. A record no listed row holds is named from
the rows' ``summary_fields`` where AWX embeds it, else ``#<id>`` when the
token cannot read it either; references to it are ``unresolved``.
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
    readable = _Readable(fk, named=_summary_names(listed))
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
    located = [
        (spec, snapshot, resource, ResourceLocation(id=path_of(spec, int(snapshot.record["id"]))))
        for (spec, snapshot), resource in zip(read, resources, strict=True)
    ]
    identities = {
        (spec.kind, int(snapshot.record["id"])): (_identity(resource), location)
        for spec, snapshot, resource, location in located
    }
    base = OfflineBase(loaded, hidden=readable.hidden)
    references = SnapshotReferences(resources, catalog=catalog, base=base)
    items = tuple(
        Linted(
            origin="controller",
            location=location,
            resource=resource,
            references=(
                *id_references(
                    spec,
                    resource,
                    snapshot.fk_ids,
                    parent_id=_parent_id(spec, snapshot.record),
                    node_ids=snapshot.node_ids,
                    listed=identities,
                ),
                *references.of(
                    resource, skip=_keys(spec, snapshot.record) | snapshot.node_ids.keys()
                ),
                *opaque_references(snapshot.opaque_nodes),
            ),
            controller=_facts(spec, snapshot.record, playbooks),
        )
        for spec, snapshot, resource, location in located
    )
    return AwxResources(items=items, kinds=kind_facts(catalog), loaded=loaded, online=True)


def _parent_id(spec: ResourceSpec, record: Mapping[str, Any]) -> int | None:
    """A schedule's parent's id: AWX's ``unified_job_template``; ``None`` for other kinds."""
    if not any(ref.polymorphic for ref in spec.fk_refs):
        return None
    value = record.get("unified_job_template")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _keys(spec: ResourceSpec, record: Mapping[str, Any]) -> frozenset[str]:
    """The keys :func:`id_references` resolves: every foreign key, the parent by its id."""
    by_id = _parent_id(spec, record) is not None
    return frozenset(ref.field for ref in spec.fk_refs if by_id or not ref.polymorphic)


def _identity(resource: Resource) -> IdentityRef:
    """The identity a live item is found by: its kind, name, organization and parent."""
    metadata = resource.metadata
    return IdentityRef(
        kind=resource.kind,
        name=metadata.name,
        organization=metadata.organization,
        parent=metadata.parent,
    )


def _summary_names(
    listed: Iterable[tuple[ResourceSpec, list[dict[str, Any]]]],
) -> dict[tuple[str, int], str]:
    """The names rows' ``summary_fields`` give records no listed row holds, by kind and id.

    AWX embeds ``{id, name}`` for what a row references; for a record the
    token cannot list (its kind refused, or that record out of its sight)
    that is the only name to be had. Names only: a summary carries no
    organization, so it never stands for an identity.
    """
    rows_of = [(spec, rows) for spec, rows in listed]
    held = {(spec.kind, row.get("id")) for spec, rows in rows_of for row in rows}
    names: dict[tuple[str, int], str] = {}
    for spec, rows in rows_of:
        keys = [ref for ref in spec.fk_refs if ref.kind is not None and not ref.polymorphic]
        for row in rows:
            summaries = row.get("summary_fields")
            if not isinstance(summaries, Mapping):
                continue
            for ref in keys:
                value = summaries.get(ref.field)
                for each in value if isinstance(value, list) else [value]:
                    if isinstance(each, Mapping) and _named(each):
                        key = (str(ref.kind), int(each["id"]))
                        if key not in held:
                            names.setdefault(key, str(each["name"]))
    return names


def _named(value: Mapping[str, Any]) -> bool:
    return (
        isinstance(value.get("id"), int)
        and not isinstance(value.get("id"), bool)
        and isinstance(value.get("name"), str)
    )


class _Readable:
    """The resolver export names references through, a record the token cannot read kept.

    AWX refuses a record's detail to a token that may not read it (403); export
    would fail on the whole run. Here such a record is named by its referrer's
    ``summary_fields`` (``named``) or ``#<id>``, asked for once at most, and
    remembered in :attr:`hidden`, so a reference to it is ``unresolved``.
    """

    def __init__(self, fk: FkResolver, *, named: Mapping[tuple[str, int], str]) -> None:
        self._fk = fk
        self._named = named
        self._refused: set[tuple[str, int]] = set()
        self.hidden: set[tuple[str, str]] = set()

    def _unread(self, kind: str, id_: int) -> str:
        name = self._named.get((kind, id_), f"#{id_}")
        self.hidden.add((kind, name))
        return name

    def id_to_name(self, kind: str, id_: int) -> str:
        if (kind, id_) in self._named or (kind, id_) in self._refused:
            return self._unread(kind, id_)
        try:
            return self._fk.id_to_name(kind, id_)
        except PermissionDeniedError:
            self._refused.add((kind, id_))
            return self._unread(kind, id_)

    def id_to_identity(self, kind: str, id_: int) -> IdentityRef:
        if (kind, id_) not in self._refused:
            try:
                return self._fk.id_to_identity(kind, id_)
            except PermissionDeniedError:
                self._refused.add((kind, id_))
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
