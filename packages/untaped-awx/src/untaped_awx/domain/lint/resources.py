"""``AwxResources``: the one snapshot every awx rule reads, from documents or the controller.

Its unit is the export document (:class:`~untaped_awx.domain.envelope.Resource`)
in both origins, so a rule sees ``project: web`` whether the resource came
from a file or from the controller. Each item carries where it was read
(its ``location``), its references with what the loader found for each, and,
from the controller, the facts only the controller knows. A document the
reader could not read is carried as :class:`Unreadable`, for a rule to
report, never raised.

A reference is ``resolved``; ``absent`` when its kind was searched in this
origin and the name is not there (what ``apply`` would fail on); or
``unresolved`` when it could not be checked (``--offline`` with no document
of its kind, an id offline, a record the token cannot read), which is not
an error. A live reference is resolved by the id the controller holds, so
its ``target`` is the identity of the item it names, wherever that lives.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from untaped.sdk import Record, experimental, kind_of
from untaped_awx.domain.envelope import IdentityRef, Resource
from untaped_lint.api import DocumentLocation, ResourceLocation, Subject

type Origin = Literal["document", "controller"]
"""Where a linted resource was read: an apply document or the controller."""

type Resolution = Literal["resolved", "absent", "unresolved"]
"""What the loader found for a reference (see the module docstring)."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


@experimental
class Reference(_Model):
    """One reference a resource makes, as written, and what its loader found."""

    field: str
    """The field holding it: ``project``, ``credentials``, ``parent``, ``nodes[deploy].run``."""
    target: IdentityRef
    """The kind and name it names, in the scope it is looked up in (an empty name: unset)."""
    resolution: Resolution


@experimental
class ControllerFacts(_Model):
    """What only the controller knows about a resource; unset under the document origin."""

    status: str | None = None
    """The resource's last job or update status (a project's last sync: ``successful``)."""
    last_job_failed: bool | None = None
    total_hosts: int | None = None
    """An inventory's host count."""
    playbooks: tuple[str, ...] | None = None
    """A successful project's playbooks at that sync; ``None`` when not read."""


@experimental
class KindFacts(_Model):
    """What the catalog knows about a kind that a rule may need."""

    known_fields: tuple[str, ...]
    """Every field untaped knows for the kind, sorted."""
    secret_paths: tuple[str, ...]
    """The kind's secret-path patterns (``survey_spec.spec.*[type=password].default``)."""
    read_only: bool = False
    """Whether ``apply`` refuses the kind's documents (``Credential``): export leaves it out."""


@experimental
class Unreadable(_Model):
    """A document the reader could not read: not YAML, not a resource, or an unknown kind."""

    path: str
    index: int
    """The document's position in its file, from 1."""
    error: str


@experimental
class Linted(_Model):
    """One resource of the snapshot: the document, where it was read and its references."""

    origin: Origin
    location: DocumentLocation | ResourceLocation
    resource: Resource
    references: tuple[Reference, ...] = ()
    controller: ControllerFacts | None = None


@experimental
class AwxResources(Record, kind="awx.resources"):
    """Every resource one run lints, with the catalog facts its rules need."""

    items: tuple[Linted, ...] = ()
    kinds: dict[str, KindFacts] = Field(default_factory=dict)
    """Every catalog kind, by name."""
    loaded: tuple[str, ...] = ()
    """The kinds searched this run: the batch's under ``--offline``, else every kind read."""
    online: bool = False
    """Whether the controller was searched: every run but ``--offline`` and the repo reader."""
    unreadable: tuple[Unreadable, ...] = ()

    _index: tuple[tuple[Linted, ...], dict[tuple[str, str], list[Linted]]] | None = PrivateAttr(
        default=None
    )

    def of(self, kind: str) -> Iterator[Linted]:
        """The items of ``kind``, in snapshot order."""
        return (item for item in self.items if item.resource.kind == kind)

    def find(self, ref: IdentityRef) -> Linted | None:
        """The one item ``ref`` names: kind and name equal, organization and parent where set.

        ``None`` when none does, or when several do: a rule never judges an
        arbitrary namesake (``apply`` refuses the ambiguity).
        """
        found = [
            item
            for item in self._by_name().get((ref.kind, ref.name), ())
            if (ref.organization is None or organization_of(item) == ref.organization)
            and (ref.parent is None or _same(item.resource.metadata.parent, ref.parent))
        ]
        return found[0] if len(found) == 1 else None

    def _by_name(self) -> dict[tuple[str, str], list[Linted]]:
        """The items by kind and name, built for the ``items`` this instance holds now.

        Keyed on the tuple itself, so a copy with other items (``model_copy``)
        never reads the original's index.
        """
        if self._index is None or self._index[0] is not self.items:
            by_name: dict[tuple[str, str], list[Linted]] = {}
            for item in self.items:
                key = (item.resource.kind, item.resource.metadata.name)
                by_name.setdefault(key, []).append(item)
            self._index = (self.items, by_name)
        return self._index[1]


def organization_of(item: Linted) -> str | None:
    """The organization ``item`` belongs to: its own, else its nearest parent's.

    A schedule's is its template's; a host's, its inventory's.
    """
    metadata = item.resource.metadata
    if metadata.organization is not None:
        return metadata.organization
    parent = metadata.parent
    while parent is not None:
        if parent.organization is not None:
            return parent.organization
        parent = parent.parent
    return None


def _same(have: IdentityRef | None, want: IdentityRef) -> bool:
    """Whether ``have`` is the parent ``want`` names (its unset parts match anything)."""
    if have is None or (have.kind, have.name) != (want.kind, want.name):
        return False
    if want.organization is not None and have.organization != want.organization:
        return False
    return want.parent is None or _same(have.parent, want.parent)


def subject_of(resources: AwxResources) -> Subject:
    """``resources`` as the lint subject the awx rules read."""
    kind = kind_of(AwxResources)
    if kind is None:  # pragma: no cover - declared above
        raise TypeError("the snapshot model declares no kind")
    return Subject(kind=kind, record=resources.model_dump(mode="json"))


__all__ = [
    "AwxResources",
    "ControllerFacts",
    "KindFacts",
    "Linted",
    "Origin",
    "Reference",
    "Resolution",
    "Unreadable",
    "organization_of",
    "subject_of",
]
