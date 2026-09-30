"""Flatten a dependency graph into one row per node reached from its root.

Pure over :class:`DependencyGraph`. Each concrete root node (the requested
ref, or every ref of a ref-less root the graph expanded; see
:func:`~untaped.capabilities.ansible.domain.graph.walk_root_ids`) is walked
breadth first, so each reported path is a shortest one and each node is
reported once per root. ``requires`` walks follow what the root depends on
(``ansible deps``); ``impacts`` walks follow who depends on it
(``ansible impact``). A row's ``declared_ref`` and ``declared_in`` come from
the edge that reached it, verbatim.
"""

from __future__ import annotations

from collections import deque
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from untaped.capabilities.ansible.domain.graph import (
    DependencyGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    StopReason,
    walk_root_ids,
)


class ReachedNode(BaseModel):
    """One node reached from a root (``ansible.dependency`` / ``ansible.dependent``).

    ``path`` reads in dependency order (each label requires the next): from
    the root for ``requires`` walks, towards the root for ``impacts`` walks.
    ``root_ref`` is the ref of the root it was reached from.
    """

    model_config = ConfigDict(frozen=True)
    table_columns: ClassVar[tuple[str, ...]] = ("repo", "ref", "stopped", "declared_in", "path")

    repo: str | None
    ref: str | None
    unresolved: str | None
    declared_ref: str | None
    declared_in: str | None
    depth: int
    path: list[str]
    root_ref: str | None
    stopped: StopReason | None


class Reach(BaseModel):
    """A reached node plus the root it was reached from."""

    model_config = ConfigDict(frozen=True)

    root: GraphNode
    node: ReachedNode


def reach(graph: DependencyGraph, relation: EdgeRelation) -> list[Reach]:
    """Every node reached from the graph's root(s) along ``relation`` edges."""
    nodes = {node.id: node for node in graph.nodes}
    upstream = relation == "impacts"
    children: dict[str, list[tuple[str, GraphEdge]]] = {}
    for edge in graph.edges:
        if edge.relation != relation:
            continue
        parent, child = (
            (edge.target_id, edge.source_id) if upstream else (edge.source_id, edge.target_id)
        )
        children.setdefault(parent, []).append((child, edge))
    hits: list[Reach] = []
    for root_id in walk_root_ids(nodes[graph.target_id], nodes, children):
        root = nodes[root_id]
        paths = {root_id: [root.label]}
        queue = deque([root_id])
        while queue:
            current = queue.popleft()
            for child_id, edge in children.get(current, ()):
                if child_id in paths:
                    continue
                node = nodes[child_id]
                path = paths[child_id] = [*paths[current], node.label]
                queue.append(child_id)
                hits.append(
                    Reach(
                        root=root,
                        node=ReachedNode(
                            repo=node.repo,
                            ref=node.ref,
                            unresolved=node.unresolved,
                            declared_ref=edge.version,
                            declared_in=edge.source_path,
                            depth=len(path) - 1,
                            path=path[::-1] if upstream else path,
                            root_ref=root.ref,
                            stopped=node.stopped,
                        ),
                    )
                )
    return hits


__all__ = ["Reach", "ReachedNode", "reach"]
