"""Flatten a dependency graph into one row per node reached from its root.

Pure over :class:`DependencyGraph`. Each concrete root node (the requested
ref, or every ref of a ref-less root the graph expanded) is walked breadth
first, so each reported path is a shortest one and each node is reported
once per root. ``requires`` walks follow what the root depends on
(``ansible deps``); ``impacts`` walks follow who depends on it
(``ansible impact``). A row's ``declared_ref`` and ``declared_in`` come from
the edge that reached it, verbatim.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

from untaped.capabilities.ansible.domain.graph import (
    DependencyGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
)
from untaped.capabilities.ansible.domain.identity import repo_key


class ReachedNode(BaseModel):
    """One node reached from a root (``ansible.dependency`` / ``ansible.dependent``).

    ``path`` reads in dependency order (each label requires the next): from
    the root for ``requires`` walks, towards the root for ``impacts`` walks.
    """

    model_config = ConfigDict(frozen=True)

    repo: str | None
    ref: str | None
    unresolved: str | None
    declared_ref: str | None
    declared_in: str | None
    depth: int
    path: list[str]


class Reach(BaseModel):
    """A reached node plus the root it was reached from."""

    model_config = ConfigDict(frozen=True)

    root: GraphNode
    node: ReachedNode


def reach(graph: DependencyGraph, relation: EdgeRelation) -> list[Reach]:
    """Every node reached from the graph's root(s) along ``relation`` edges."""
    return list(_walk(graph, relation))


def _walk(graph: DependencyGraph, relation: EdgeRelation) -> Iterator[Reach]:
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
    for root in _root_nodes(graph, nodes, children):
        reached: dict[str, tuple[str, GraphEdge] | None] = {root.id: None}
        queue = deque([root.id])
        while queue:
            current = queue.popleft()
            for child_id, edge in children.get(current, ()):
                if child_id in reached:
                    continue
                reached[child_id] = (current, edge)
                queue.append(child_id)
                path = _path(child_id, reached, nodes)
                node = nodes[child_id]
                yield Reach(
                    root=root,
                    node=ReachedNode(
                        repo=node.repo,
                        ref=node.ref,
                        unresolved=node.unresolved,
                        declared_ref=edge.version,
                        declared_in=edge.source_path,
                        depth=len(path) - 1,
                        path=path[::-1] if upstream else path,
                    ),
                )


def _root_nodes(
    graph: DependencyGraph,
    nodes: dict[str, GraphNode],
    children: dict[str, list[tuple[str, GraphEdge]]],
) -> list[GraphNode]:
    """The requested root, or each concrete ref walked for a ref-less root."""
    target = nodes[graph.target_id]
    if target.ref is not None or target.repo is None:
        return [target]
    concrete = [
        node
        for node in graph.nodes
        if node.repo is not None
        and node.ref is not None
        and repo_key(node.repo) == repo_key(target.repo)
        and node.id in children
    ]
    if target.id in children:
        return [target, *concrete]
    return concrete or [target]


def _path(
    node_id: str, reached: dict[str, tuple[str, GraphEdge] | None], nodes: dict[str, GraphNode]
) -> list[str]:
    """Labels from the walk's root to ``node_id``."""
    labels = [nodes[node_id].label]
    step = reached[node_id]
    while step is not None:
        parent, _ = step
        labels.append(nodes[parent].label)
        step = reached[parent]
    return labels[::-1]


__all__ = ["Reach", "ReachedNode", "reach"]
