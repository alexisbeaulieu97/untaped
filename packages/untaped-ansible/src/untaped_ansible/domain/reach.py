"""Flatten a dependency graph into one row per node reached from its root.

Pure over :class:`DependencyGraph`. Each concrete root node (the requested
ref, or every ref of a ref-less root the graph expanded; see
:func:`~untaped_ansible.domain.graph.walk_root_ids`) is walked
breadth first, so each reported path is a shortest one and each node is
reported once per root. ``requires`` walks follow what the root depends on
(``ansible deps``); ``impacts`` walks follow who depends on it
(``ansible impact``). A row's ``declared_ref`` and ``declared_in`` come from
the edge that reached it, verbatim.
"""

from __future__ import annotations

from collections import deque

from pydantic import BaseModel, ConfigDict

from untaped_ansible.domain.graph import (
    DependencyGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    walk_root_ids,
)
from untaped_ansible.records import ReachedRow


class Reach(BaseModel):
    """A reached node's row, the root it was reached from and the commits it was indexed at.

    ``shas`` are the commits of the row's repo (see :attr:`GraphNode.shas`):
    never on the wire, so they sit beside the row, not in it.
    """

    model_config = ConfigDict(frozen=True)

    root: GraphNode
    row: ReachedRow
    shas: frozenset[str] = frozenset()


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
                        row=ReachedRow(
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
                        shas=node.shas,
                    )
                )
    return hits


__all__ = ["Reach", "reach"]
