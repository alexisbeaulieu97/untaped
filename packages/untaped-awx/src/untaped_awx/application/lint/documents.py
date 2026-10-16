"""The documents loader: apply documents as an ``AwxResources`` snapshot.

``FILES``, the repository reader and ``untaped_awx.testing.resources`` all
load through :func:`load_documents`, so a document lints the same wherever
it is read. Documents are linted as written: ``awx.default_organization``
is not applied, so the same files give the same targets on every profile.
"""

from __future__ import annotations

from collections.abc import Iterable

from untaped.sdk import ConfigError
from untaped_awx.application.lint.resolution import OfflineBase, SnapshotReferences
from untaped_awx.application.lint.snapshot import kind_facts
from untaped_awx.application.resources.apply_prefetch import prefetch_plan
from untaped_awx.application.resources.ports import Catalog, FkResolver
from untaped_awx.domain import Resource
from untaped_awx.domain.lint.resources import AwxResources, Linted, Unreadable
from untaped_lint.api import DocumentLocation

type Document = tuple[str, int, Resource | str]
"""A document as read: its file's label, its index in the file from 1, and the resource or why
it could not be read."""


def load_documents(
    documents: Iterable[Document], *, catalog: Catalog, live: FkResolver | None = None
) -> AwxResources:
    """The snapshot of ``documents``: each a :class:`Linted` or an :class:`Unreadable`.

    Without ``live`` (``--offline``) a reference resolves in the batch only,
    and a kind with no document in the batch is not searched. With it, a
    reference the batch lacks is looked up on the controller, after one
    prefetch of every lookup the batch makes; a kind the token may not list
    is left out of ``loaded``.
    """
    read: list[tuple[str, int, Resource]] = []
    unreadable: list[Unreadable] = []
    for label, index, document in documents:
        if isinstance(document, str):
            unreadable.append(Unreadable(path=label, index=index, error=document))
            continue
        try:
            catalog.get(document.kind)
        except ConfigError as exc:
            unreadable.append(Unreadable(path=label, index=index, error=str(exc)))
            continue
        read.append((label, index, document))
    resources = [resource for _label, _index, resource in read]
    if live is None:
        batch = {resource.kind for resource in resources}
        loaded = tuple(kind for kind in catalog.kinds() if kind in batch)
        base: FkResolver = OfflineBase(loaded)
    else:
        live.prefetch(prefetch_plan(resources, catalog=catalog))
        base = live
    references = SnapshotReferences(resources, catalog=catalog, base=base)
    items = tuple(
        Linted(
            origin="document",
            location=DocumentLocation(path=label, index=index),
            resource=resource,
            references=references.of(resource),
        )
        for label, index, resource in read
    )
    if live is not None:  # every kind was searched, but those the token may not list
        loaded = tuple(kind for kind in catalog.kinds() if kind not in references.denied)
    return AwxResources(
        items=items,
        kinds=kind_facts(catalog),
        loaded=loaded,
        online=live is not None,
        unreadable=tuple(unreadable),
    )


__all__ = ["Document", "load_documents"]
