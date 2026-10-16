"""What each reference of a lint snapshot resolves to, in apply's order.

A document's reference is looked up as ``apply`` would: among the
snapshot's own items first (:class:`PlanningFkResolver` over their planned
targets), then in a base. :class:`OfflineBase` is the base that searches
nothing past the items: ``--offline`` and the repository reader use it. A
document batch linted online uses the live resolver instead. The controller
snapshot resolves its foreign keys by id (:func:`id_references`), and only
a schedule's parent and its workflow nodes' references, which export names
with their organization, by name over :class:`OfflineBase`.

What a lookup raises becomes the reference's :data:`Resolution`: a match, or
several (ambiguity is ``apply``'s error, not a missing resource), is
``resolved``; a miss in a searched kind is ``absent``; a kind nobody
searched, or one the token cannot read, is ``unresolved``.
"""

from __future__ import annotations

from collections.abc import Container, Iterable, Iterator, Mapping, Sequence
from typing import Any

from untaped_awx.application.resources.apply_planner import ApplyPlanner
from untaped_awx.application.resources.mutation_planning import (
    PlanningFkResolver,
    planned_target,
)
from untaped_awx.application.resources.ports import Catalog, FkResolver
from untaped_awx.application.resources.references import ReferenceSite, references_of
from untaped_awx.domain import IdentityRef, Resource, ResourceSpec
from untaped_awx.domain.lint.resources import Reference, Resolution
from untaped_awx.domain.workflow_graph import NameRef, NodeRun
from untaped_awx.errors import (
    AmbiguousIdentityError,
    AwxError,
    BadRequestError,
    PermissionDeniedError,
    ResourceNotFoundError,
)

_REQUIRED: dict[str, tuple[str, ...]] = {"JobTemplate": ("project", "inventory")}
"""Kind → the references a launch cannot do without (an ``inventory`` can be asked for)."""


class _NotSearchedError(AwxError):
    """The base did not search the kind: the reference is ``unresolved``, never raised further."""


class OfflineBase:
    """A resolver that knows nothing past the snapshot's items.

    A kind in ``searched`` was read whole, so a name the items lack is not
    there (``absent``); any other kind, or an id, was not searched. A name in
    ``hidden`` stands for a record the token could not read (the controller
    loader names it ``#<id>``): it was not searched either.
    """

    def __init__(self, searched: Iterable[str], *, hidden: Iterable[tuple[str, str]] = ()) -> None:
        self._searched = frozenset(searched)
        self._hidden = frozenset(hidden)

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        if kind in self._searched and (kind, name) not in self._hidden:
            raise ResourceNotFoundError(kind, {"name": name, **(scope or {})})
        raise _NotSearchedError(kind)

    def validate_id(self, kind: str, id_: int, *, scope: dict[str, str] | None = None) -> int:
        raise _NotSearchedError(kind)

    def id_to_identity(self, kind: str, id_: int) -> IdentityRef:
        raise _NotSearchedError(kind)

    def id_to_name(self, kind: str, id_: int) -> str:
        raise _NotSearchedError(kind)

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        reference = IdentityRef.model_validate(value)
        return reference.kind, self.name_to_id(reference.kind, reference.name)

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        """Nothing to warm."""

    def seed(self, kind: str, records: Iterable[Mapping[str, Any]]) -> None:
        """Nothing to learn."""


class SnapshotReferences:
    """Resolves the references of ``resources``: among them first, then in ``base``."""

    def __init__(
        self, resources: Sequence[Resource], *, catalog: Catalog, base: FkResolver
    ) -> None:
        planner = ApplyPlanner()
        targets = [
            planned_target(index, resource, spec=catalog.get(resource.kind), planner=planner)
            for index, resource in enumerate(resources)
        ]
        self._fk = PlanningFkResolver(base, targets)
        self._catalog = catalog
        self.denied: set[str] = set()
        """The kinds a lookup was refused for (403): the token cannot list them."""

    def of(self, resource: Resource, *, skip: Container[str] = ()) -> tuple[Reference, ...]:
        """Each reference ``resource`` makes, with what its lookup found.

        ``skip`` names fields the caller resolves itself (the controller
        loader, by id); their empty values still count (P8).
        """
        spec = self._catalog.get(resource.kind)
        try:
            sites = references_of(spec, resource)
        except BadRequestError:
            sites = ()  # an ancestry apply refuses before any lookup; nothing to look up
        return (
            *(self._resolve(site) for site in sites if site.field not in skip),
            *empty_references(spec, resource),
        )

    def _resolve(self, site: ReferenceSite) -> Reference:
        try:
            target, scope = _lookup(site)
        except ValueError, BadRequestError:  # an ancestry apply refuses: nothing it can find
            target = IdentityRef(kind=site.kind, name=_name(site.value))
            return Reference(field=site.field, target=target, resolution="absent")
        resolution: Resolution
        try:
            if isinstance(site.value, int) and not isinstance(site.value, bool):
                self._fk.validate_id(site.kind, site.value, scope=scope)
            else:
                self._fk.name_to_id(target.kind, target.name, scope=scope)
        except AmbiguousIdentityError:
            resolution = "resolved"
        except _NotSearchedError:
            resolution = "unresolved"
        except PermissionDeniedError:
            self.denied.add(target.kind)
            resolution = "unresolved"
        except ResourceNotFoundError, BadRequestError:
            resolution = "absent"
        else:
            resolution = "resolved"
        return Reference(field=site.field, target=target, resolution=resolution)


def id_references(
    spec: ResourceSpec,
    resource: Resource,
    fk_ids: Mapping[str, Any],
    *,
    listed: Mapping[tuple[str, int], IdentityRef],
) -> Iterator[Reference]:
    """A live resource's foreign keys, each resolved by the id the controller holds.

    A name would mislead here: export writes a key by its bare name and
    ``apply`` would look it up in the referrer's organization, so a project
    in another organization, or a credential in none, would read as missing.
    An id ``listed`` holds is ``resolved``, its target the identity of the
    item it names; any other id (a kind the token cannot list, a record it
    cannot see) is ``unresolved``. An empty key is :func:`empty_references`'.
    """
    for ref in spec.fk_refs:
        if ref.polymorphic or ref.kind is None or ref.field not in resource.spec:
            continue
        held = fk_ids.get(ref.field)
        ids = held if isinstance(held, list) else [held]
        written = resource.spec[ref.field]
        names = written if isinstance(written, list) else [written]
        for position, id_ in enumerate(ids):
            if not isinstance(id_, int) or isinstance(id_, bool):
                continue
            identity = listed.get((ref.kind, id_))
            if identity is not None:
                yield Reference(field=ref.field, target=identity, resolution="resolved")
                continue
            name = _name(names[position]) if position < len(names) else f"#{id_}"
            target = IdentityRef(kind=ref.kind, name=name)
            yield Reference(field=ref.field, target=target, resolution="unresolved")


def empty_references(spec: ResourceSpec, resource: Resource) -> Iterator[Reference]:
    """The required references ``resource`` writes as ``null``: each ``absent``, unnamed.

    A field the document leaves out is not empty: ``apply`` keeps what the
    controller has. An empty ``inventory`` counts only beside
    ``ask_inventory_on_launch: false``: a template that asks for one on
    launch may have none, and a partial document that leaves the flag out
    may be asking.
    """
    body = resource.spec
    kinds = {ref.field: ref.kind for ref in spec.fk_refs if ref.kind is not None}
    for field in _REQUIRED.get(resource.kind, ()):
        if field not in body or body[field] is not None:
            continue
        if field == "inventory" and body.get("ask_inventory_on_launch") is not False:
            continue
        target = IdentityRef(kind=kinds[field], name="")
        yield Reference(field=field, target=target, resolution="absent")


def opaque_references(identifiers: Iterable[str]) -> Iterator[Reference]:
    """A workflow's nodes that run nothing untaped can name, each an ``absent`` reference."""
    for identifier in identifiers:
        target = IdentityRef(kind="UnifiedJobTemplate", name="")
        yield Reference(field=f"nodes[{identifier}]", target=target, resolution="absent")


def _lookup(site: ReferenceSite) -> tuple[IdentityRef, dict[str, str] | None]:
    """The identity ``site`` names and the scope its lookup runs in, as apply computes them."""
    value = site.value
    scope = site.scope
    if isinstance(value, Mapping):
        reference = IdentityRef.model_validate({"kind": site.kind, **value})
        scope = reference.lookup_scope(scope) or None
        return _identity(reference.kind, reference.name, scope), scope
    return _identity(site.kind, _name(value), scope), scope


def _identity(kind: str, name: str, scope: Mapping[str, str] | None) -> IdentityRef:
    """``kind``/``name`` in ``scope``: its organization, and the inventory holding it."""
    scope = scope or {}
    parent = None
    if "inventory" in scope:
        parent = IdentityRef(
            kind="Inventory",
            name=scope["inventory"],
            organization=scope.get("inventory__organization"),
        )
    return IdentityRef(kind=kind, name=name, organization=scope.get("organization"), parent=parent)


def _name(value: Any) -> str:
    if isinstance(value, NodeRun | NameRef):
        return value.name
    if isinstance(value, Mapping):
        return str(value.get("name", ""))
    return str(value)


__all__ = [
    "OfflineBase",
    "SnapshotReferences",
    "empty_references",
    "id_references",
    "opaque_references",
]
