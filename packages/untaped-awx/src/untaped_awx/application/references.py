"""Every reference a resource document makes to another resource, as apply reads it.

:func:`references_of` is the one walk of a document's references: its spec's
foreign keys (each value of a multi-reference on its own), the polymorphic
parent a schedule keeps in ``metadata.parent``, and what each workflow node runs
and prompts with. Each :class:`ReferenceSite` carries the scope apply resolves
it in, so a caller that warms a cache (``apply_prefetch``) or checks that a
reference exists looks in the same place apply will.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from untaped_awx.application.apply_planner import scope_for
from untaped_awx.application.workflow_graph import node_lookup_scope
from untaped_awx.domain import Resource, ResourceSpec
from untaped_awx.domain.payloads import as_dict
from untaped_awx.domain.workflow_graph import (
    PROMPT_MEMBERS,
    PROMPT_REFERENCES,
    WorkflowNodeSpec,
    parse_workflow_nodes,
)


@dataclass(frozen=True)
class ReferenceSite:
    """One reference as written: where it sits, the kind it names, and its lookup scope.

    ``field`` is the spec field (``project``, ``credentials``), ``parent`` for a
    polymorphic parent, or ``nodes[<id>].run`` / ``nodes[<id>].prompts.<field>``
    for a workflow node. ``value`` is as written: a name, an id, a
    ``{name, organization}`` mapping, a parent's identity mapping, or a node's
    :class:`~untaped_awx.domain.workflow_graph.NodeRun` or prompt reference.
    ``scope`` is the scope apply passes when it resolves the value: an FK's
    :func:`scope_for`, a node value's
    :func:`~untaped_awx.application.workflow_graph.node_lookup_scope`, ``None``
    for a parent.
    """

    field: str
    kind: str
    value: Any
    scope: dict[str, str] | None


def references_of(spec: ResourceSpec, resource: Resource) -> tuple[ReferenceSite, ...]:
    """Each reference ``resource`` makes, in its spec's ``fk_refs`` order, then its nodes'."""
    body = resource.spec if isinstance(resource.spec, dict) else {}
    sites: list[ReferenceSite] = []
    for ref in spec.fk_refs:
        if ref.polymorphic:
            # Export keeps a polymorphic parent in metadata.parent, and apply
            # resolves it from there; a spec-level value is dropped.
            parent = resource.metadata.parent
            if parent is not None:
                sites.append(ReferenceSite(ref.field, parent.kind, as_dict(parent), None))
            continue
        if ref.kind is None or ref.field not in body or body[ref.field] is None:
            continue
        scope = scope_for(ref, resource)
        value = body[ref.field]
        values = value if ref.multi and isinstance(value, list) else [value]
        sites.extend(ReferenceSite(ref.field, ref.kind, item, scope) for item in values)
    if spec.node_field is not None and spec.node_field in body:
        sites.extend(_node_sites(body[spec.node_field], resource))
    return tuple(sites)


def _node_sites(value: Any, resource: Resource) -> list[ReferenceSite]:
    """What each node runs and prompts with; none for an invalid graph (apply refuses it)."""
    try:
        nodes = parse_workflow_nodes(value)
    except ValueError:
        return []
    organization = resource.metadata.organization
    default = {"organization": organization} if organization else None
    return [site for node in nodes for site in _sites_of(node, default)]


def _sites_of(node: WorkflowNodeSpec, default: dict[str, str] | None) -> list[ReferenceSite]:
    prefix = f"nodes[{node.id}]"
    found: list[tuple[str, str, Any]] = []
    if node.run is not None:
        found.append((f"{prefix}.run", node.run.kind, node.run))
    for name, kind in PROMPT_REFERENCES.items():
        if (reference := getattr(node.prompts, name)) is not None:
            found.append((f"{prefix}.prompts.{name}", kind, reference))
    for name, kind in PROMPT_MEMBERS.items():
        found.extend(
            (f"{prefix}.prompts.{name}", kind, member)
            for member in getattr(node.prompts, name) or ()
        )
    return [
        ReferenceSite(field, kind, value, node_lookup_scope(kind, value, default=default))
        for field, kind, value in found
    ]


__all__ = ["ReferenceSite", "references_of"]
