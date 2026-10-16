"""The controller loader: every resource the token can read, as an ``AwxResources`` snapshot.

Two passes. The first lists each catalog kind once and seeds the resolver
with the rows, so the second, which turns each row into the document
``export`` would write, names its references from those rows instead of a
read per reference. Every kind is read, ``export``'s read-only ones too: a
rule may judge what ``apply`` cannot write.

A kind the token may not list is left out: its references are
``unresolved``, not ``absent``. Each document's secret-named launch and
inventory variables are redacted before it joins the snapshot, since the
snapshot is cached on disk.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from untaped.sdk import NO_PROGRESS, ProgressHandle
from untaped_awx.application.lint.resolution import (
    OfflineBase,
    SnapshotReferences,
    opaque_references,
)
from untaped_awx.application.lint.snapshot import kind_facts
from untaped_awx.application.resources.ports import (
    Catalog,
    FkResolver,
    ProjectPlaybooks,
    ResourceClient,
    WorkflowNodeRepository,
)
from untaped_awx.application.resources.save_resource import ResourceSnapshot, SaveResource
from untaped_awx.domain import ResourceSpec
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
    save = SaveResource(client, fk, nodes=nodes)
    total = sum(len(rows) for _spec, rows in listed)
    read: list[tuple[ResourceSpec, ResourceSnapshot]] = []
    for spec, rows in listed:
        for row in rows:
            progress.update(
                f"{spec.kind} {row.get('name')}", phase="reading", done=len(read), total=total
            )
            read.append((spec, save.snapshot_from_record(spec, row)))
    progress.update(None, done=total, total=total)
    loaded = tuple(spec.kind for spec, _rows in listed)
    resources = [redact_variables(snapshot.resource) for _spec, snapshot in read]
    references = SnapshotReferences(resources, catalog=catalog, base=OfflineBase(loaded))
    items = tuple(
        Linted(
            origin="controller",
            location=ResourceLocation(id=path_of(spec, int(snapshot.record["id"]))),
            resource=resource,
            references=(
                *references.of(resource),
                *opaque_references(snapshot.opaque_nodes),
            ),
            controller=_facts(spec, snapshot.record, playbooks),
        )
        for (spec, snapshot), resource in zip(read, resources, strict=True)
    )
    return AwxResources(items=items, kinds=kind_facts(catalog), loaded=loaded, online=True)


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
